"""
Quality Manager - Auto delete lower quality prints when higher quality is available
Features:
- Detect quality type from filename
- Auto delete theatre prints (CamRip, HDTC, HDTS, PreDVD) when high quality (WebDL, WebRip, BluRay, HDRip) available
- Maintain best quality version per title
"""

import re
import logging
from typing import Optional, Tuple, List
from database.ia_filterdb import Media
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

# Quality hierarchy (lower number = lower quality, should be deleted first)
QUALITY_HIERARCHY = {
    # Theatre/Cam Quality (Lowest)
    "camrip": 1,
    "hdcam": 1,
    "hdtc": 2,
    "hdts": 2,
    "ts": 2,
    "tc": 2,
    "telesync": 2,
    "predvd": 3,
    "dvdscr": 3,
    
    # Standard Quality
    "dvdrip": 4,
    "tvrip": 5,
    "hdtv": 5,
    
    # Web Quality (Medium)
    "webrip": 6,
    "web-dl": 7,
    "webdl": 7,
    "web dl": 7,
    
    # Premium Quality (Highest)
    "hdrip": 8,
    "bluray": 9,
    "bdrip": 9,
    "brrip": 9,
}

# Resolution quality mapping
RESOLUTION_HIERARCHY = {
    "240p": 1,
    "140p": 1,
    "360p": 2,
    "480p": 3,
    "540p": 4,
    "720p": 5,
    "1080p": 6,
    "1440p": 7,
    "2160p": 8,
    "4k": 8,
}


def extract_quality_info(filename: str, caption: str = "") -> dict:
    """
    Extract quality information from filename and caption
    
    Returns:
        {
            'source': str (e.g., 'WebDL', 'BluRay', 'CamRip'),
            'resolution': str (e.g., '1080p', '720p'),
            'quality_score': int (lower = worse quality),
            'source_score': int,
            'resolution_score': int
        }
    """
    text = f"{filename} {caption}".lower()
    
    quality_info = {
        'source': None,
        'resolution': None,
        'quality_score': 0,
        'source_score': 0,
        'resolution_score': 0
    }
    
    # Extract source quality
    for source, score in QUALITY_HIERARCHY.items():
        pattern = rf'\b{re.escape(source)}\b'
        if re.search(pattern, text):
            quality_info['source'] = source
            quality_info['source_score'] = score
            break
    
    # Extract resolution
    for res, score in RESOLUTION_HIERARCHY.items():
        pattern = rf'\b{re.escape(res)}\b'
        if re.search(pattern, text):
            quality_info['resolution'] = res
            quality_info['resolution_score'] = score
            break
    
    # Calculate overall quality score (source weight 70%, resolution weight 30%)
    quality_info['quality_score'] = (quality_info['source_score'] * 0.7) + (quality_info['resolution_score'] * 0.3)
    
    return quality_info


def is_low_quality_print(quality_info: dict) -> bool:
    """
    Check if the quality is a lower quality theatre print
    Returns True if it's CamRip, HDTC, HDTS, PreDVD
    """
    low_quality_sources = ['camrip', 'hdcam', 'hdtc', 'hdts', 'ts', 'tc', 'telesync', 'predvd']
    return quality_info.get('source') in low_quality_sources


def is_high_quality(quality_info: dict) -> bool:
    """
    Check if the quality is high quality (WebDL, WebRip, BluRay, HDRip)
    """
    high_quality_sources = ['webrip', 'web-dl', 'webdl', 'web dl', 'hdrip', 'bluray', 'bdrip', 'brrip']
    return quality_info.get('source') in high_quality_sources


def should_delete_existing(existing_quality: dict, new_quality: dict) -> bool:
    """
    Determine if existing file should be deleted based on new file's quality
    
    Delete existing if:
    1. New file is higher quality than existing
    2. New file is high quality (WebDL, BluRay, HDRip) and existing is low quality (CamRip, HDTC, etc)
    
    Returns: True if existing should be deleted, False otherwise
    """
    # If new quality is significantly better (score difference > 2)
    if new_quality['quality_score'] > existing_quality['quality_score'] + 2:
        return True
    
    # If new is high quality and existing is low quality
    if is_high_quality(new_quality) and is_low_quality_print(existing_quality):
        return True
    
    # Same source, but new has better resolution
    if new_quality['source'] == existing_quality['source']:
        if new_quality['resolution_score'] > existing_quality['resolution_score']:
            return True
    
    return False


def get_base_title(filename: str) -> str:
    """
    Extract base title from filename for matching different quality versions
    Removes quality indicators, resolution, source, etc.
    
    Example:
    'Movie Name 2023 1080p WebDL AAC' -> 'Movie Name 2023'
    'Movie Name 2023 720p CamRip' -> 'Movie Name 2023'
    """
    text = filename.lower()
    
    # Remove all quality indicators
    for quality in list(QUALITY_HIERARCHY.keys()) + list(RESOLUTION_HIERARCHY.keys()):
        text = re.sub(rf'\b{re.escape(quality)}\b', '', text, flags=re.IGNORECASE)
    
    # Remove common codecs and tags
    text = re.sub(r'\b(hevc|x265|x264|h\.?264|avc|av1|aac|flac|dts|dub|sub|hindi|english|tam|tel|hin)\b', '', text, flags=re.IGNORECASE)
    
    # Remove extra spaces and special characters
    text = re.sub(r'[\[\(\{].*?[\]\)\}]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text


async def find_and_delete_lower_quality(
    db_collection,
    new_filename: str,
    new_caption: str = "",
    file_id: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Find and delete lower quality versions of the same movie/series
    
    Args:
        db_collection: MongoDB collection for media files
        new_filename: Filename of newly added file
        new_caption: Caption of newly added file
        file_id: File ID to exclude from deletion (the new file)
    
    Returns:
        (success: bool, message: str)
    """
    try:
        # Extract quality info for new file
        new_quality = extract_quality_info(new_filename, new_caption)
        
        # If new file doesn't have quality info, skip deletion logic
        if not new_quality['source']:
            return True, "No quality info found in new file, skipping cleanup"
        
        # Get base title
        base_title = get_base_title(new_filename)
        
        if not base_title:
            return True, "Could not extract title for comparison"
        
        # Search for similar files in database
        # Adjust this based on your actual database schema
        search_query = {
            'file_name': {'$regex': f'.*{re.escape(base_title)}.*', '$options': 'i'}
        }
        
        if file_id:
            search_query['file_id'] = {'$ne': file_id}
        
        similar_files = await db_collection.find(search_query).to_list(None)
        
        deleted_count = 0
        deleted_files = []
        
        for existing_file in similar_files:
            existing_filename = existing_file.get('file_name', '')
            existing_caption = existing_file.get('caption', '')
            existing_quality = extract_quality_info(existing_filename, existing_caption)
            
            # Check if we should delete this file
            if should_delete_existing(existing_quality, new_quality):
                try:
                    # Delete from database
                    await db_collection.delete_one({'_id': existing_file['_id']})
                    deleted_count += 1
                    deleted_files.append(existing_filename)
                    logger.info(f"Deleted lower quality file: {existing_filename}")
                except Exception as e:
                    logger.error(f"Error deleting file {existing_filename}: {e}")
        
        if deleted_count > 0:
            message = f"Deleted {deleted_count} lower quality files: {', '.join(deleted_files[:3])}"
            if deleted_count > 3:
                message += f" and {deleted_count - 3} more"
            return True, message
        else:
            return True, "No lower quality files found to delete"
    
    except Exception as e:
        logger.error(f"Error in find_and_delete_lower_quality: {e}")
        return False, f"Error during quality cleanup: {str(e)}"


async def cleanup_duplicates(
    db_collection,
    base_title: str,
    keep_highest_quality: bool = True
) -> Tuple[int, List[str]]:
    """
    Find and optionally delete duplicate files of the same movie/series
    Keeps only the highest quality version
    
    Args:
        db_collection: MongoDB collection
        base_title: Base title to search for duplicates
        keep_highest_quality: If True, delete lower quality duplicates
    
    Returns:
        (deleted_count: int, deleted_files: List[str])
    """
    try:
        search_query = {
            'file_name': {'$regex': f'.*{re.escape(base_title)}.*', '$options': 'i'}
        }
        
        files = await db_collection.find(search_query).to_list(None)
        
        if len(files) <= 1:
            return 0, []
        
        # Score all files
        scored_files = []
        for file in files:
            quality = extract_quality_info(file.get('file_name', ''), file.get('caption', ''))
            scored_files.append({
                'file': file,
                'quality': quality,
                'score': quality['quality_score']
            })
        
        # Sort by quality score (highest first)
        scored_files.sort(key=lambda x: x['score'], reverse=True)
        
        deleted_count = 0
        deleted_files = []
        
        # Keep the highest quality, delete the rest
        if keep_highest_quality:
            for scored_file in scored_files[1:]:
                try:
                    await db_collection.delete_one({'_id': scored_file['file']['_id']})
                    deleted_count += 1
                    deleted_files.append(scored_file['file'].get('file_name', 'Unknown'))
                    logger.info(f"Deleted duplicate: {scored_file['file'].get('file_name', '')}")
                except Exception as e:
                    logger.error(f"Error deleting duplicate: {e}")
        
        return deleted_count, deleted_files
    
    except Exception as e:
        logger.error(f"Error in cleanup_duplicates: {e}")
        return 0, []


# Command handler for manual quality cleanup
async def quality_cleanup_handler(client, message):
    """
    Manual command to trigger quality cleanup: /cleanup_quality
    Admin only command
    """
    if message.from_user.id not in [ADMIN_ID]:  # Add your admin IDs
        return
    
    try:
        await message.reply_text("🔄 Starting quality cleanup process...")
        # Implementation will depend on your database structure
        # This is a template
        await message.edit_text("✅ Quality cleanup completed!")
    except Exception as e:
        logger.error(f"Error in quality_cleanup_handler: {e}")
        await message.reply_text(f"❌ Error: {str(e)}")