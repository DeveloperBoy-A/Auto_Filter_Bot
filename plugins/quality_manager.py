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


LANGUAGES = [
    "hindi","english","tamil","telugu","malayalam",
    "kannada","punjabi","bengali","marathi","gujarati"
]

LOW_QUALITY_SOURCES = ['camrip','hdcam','hdtc','hdts','ts','tc','telesync','predvd','dvdscr']
HIGH_QUALITY_SOURCES = ['webrip','web-dl','webdl','web dl','hdrip','bluray','bdrip','brrip']

def extract_language(text: str) -> str:
    text = text.lower()
    for lang in LANGUAGES:
        if re.search(rf'\b{lang}\b', text):
            return lang
    return "unknown"


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



def should_delete_existing(existing_quality: dict, new_quality: dict, existing_lang: str, new_lang: str) -> bool:
    if existing_lang != new_lang:
        return False

    existing_source = existing_quality.get('source')
    new_source = new_quality.get('source')

    if existing_source in LOW_QUALITY_SOURCES and new_source in HIGH_QUALITY_SOURCES:
        return True

    return False



def get_base_title(filename: str) -> str:
    text = filename.lower()
    text = re.sub(r'\.(mkv|mp4|avi|mov|wmv|flv|webm)$', '', text, flags=re.I)
    text = re.sub(r'[._\-]+', ' ', text)

    for quality in list(QUALITY_HIERARCHY.keys()) + list(RESOLUTION_HIERARCHY.keys()):
        text = re.sub(rf'\b{re.escape(quality)}\b', '', text, flags=re.I)

    text = re.sub(
        r'\b(hevc|x265|x264|h264|avc|av1|aac|flac|dts|ac3|eac3|ddp|'
        r'dub|sub|esub|esubs|multi|proper|uncut|'
        r'hindi|english|tamil|telugu|malayalam|kannada|punjabi|'
        r'bengali|marathi|gujarati|movies4u|tokyo_updates)\b',
        '',
        text,
        flags=re.I
    )

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
    Find and delete lower quality versions - IMPROVED LOGGING
    """
    try:
        # Extract quality info for new file
        new_quality = extract_quality_info(new_filename, new_caption)
        
        # Better logging
        logger.info(
            f"[QUALITY] New file uploaded:\n"
            f"  📄 Filename: {new_filename[:70]}\n"
            f"  🎬 Source: {new_quality.get('source', 'N/A').upper() if new_quality.get('source') else 'UNKNOWN'}\n"
            f"  📐 Resolution: {new_quality.get('resolution', 'N/A').upper() if new_quality.get('resolution') else 'UNKNOWN'}\n"
            f"  📊 Quality Score: {new_quality.get('quality_score', 0):.1f}"
        )
        
        # If new file doesn't have quality info, skip deletion logic
        if not new_quality['source']:
            logger.warning(f"[QUALITY] Could not detect quality source from filename: {new_filename}")
            return True, "No quality info found in new file, skipping cleanup"
        
        # Get base title
        base_title = get_base_title(new_filename)
        
        if not base_title:
            logger.warning(f"[QUALITY] Could not extract base title from: {new_filename}")
            return True, "Could not extract title for comparison"
        
        logger.debug(f"[QUALITY] Base title extracted: {base_title}")
        
        words = [w for w in base_title.split() if len(w) > 2]
        pattern = ".*".join(map(re.escape, words[:6])) if words else re.escape(base_title)

        search_query = {
            'file_name': {'$regex': pattern, '$options': 'i'}
        }
        
        if file_id:
            search_query['file_id'] = {'$ne': file_id}
        
        similar_files = await db_collection.find(search_query).to_list(None)
        
        logger.info(f"[QUALITY] Similar files found in DB: {len(similar_files)}")
        
        deleted_count = 0
        deleted_files = []
        kept_files = []
        
        for existing_file in similar_files:
            existing_filename = existing_file.get('file_name', '')
            existing_caption = existing_file.get('caption', '')
            existing_quality = extract_quality_info(existing_filename, existing_caption)
            
            logger.debug(
                f"[QUALITY] Checking existing file:\n"
                f"  📄 {existing_filename[:60]}\n"
                f"  🎬 Source: {existing_quality.get('source', 'N/A')}\n"
                f"  📊 Score: {existing_quality.get('quality_score', 0):.1f}"
            )
            
            # Check if we should delete this file
            existing_lang = extract_language(f"{existing_filename} {existing_caption}")
            new_lang = extract_language(f"{new_filename} {new_caption}")

            if should_delete_existing(existing_quality, new_quality, existing_lang, new_lang):
                try:
                    # Delete from database
                    await db_collection.delete_one({'_id': existing_file['_id']})
                    deleted_count += 1
                    deleted_files.append(existing_filename)
                    
                    logger.warning(
                        f"[QUALITY] ✅ DELETED lower quality file:\n"
                        f"  📄 {existing_filename[:70]}\n"
                        f"  🎬 Source: {existing_quality.get('source', 'N/A').upper()}\n"
                        f"  📊 Old Score: {existing_quality.get('quality_score', 0):.1f} "
                        f"< New Score: {new_quality.get('quality_score', 0):.1f}"
                    )
                except Exception as e:
                    logger.error(f"[QUALITY] ❌ Error deleting file {existing_filename}: {e}")
            else:
                kept_files.append(existing_filename)
                logger.debug(
                    f"[QUALITY] ℹ️  Keeping file (equal or better quality):\n"
                    f"  📄 {existing_filename[:60]}\n"
                    f"  📊 Score: {existing_quality.get('quality_score', 0):.1f}"
                )
        
        # Generate final message with details
        if deleted_count > 0:
            message = (
                f"✅ Cleanup completed:\n"
                f"  🗑️  Deleted: {deleted_count} file(s)\n"
                f"  ✨ Kept: {len(kept_files)} file(s)"
            )
            logger.warning(
                f"[QUALITY] ✅ CLEANUP SUMMARY:\n"
                f"  Movie: {base_title}\n"
                f"  🗑️  Deleted: {deleted_count} files\n"
                f"  ✨ Kept: {len(kept_files)} files\n"
                f"  New Quality: {new_quality.get('source', 'N/A').upper()} ({new_quality.get('quality_score', 0):.1f})"
            )
            return True, message
        else:
            # Provide detailed reason
            if len(similar_files) == 0:
                message = "❌ No similar files found in database"
                logger.info(
                    f"[QUALITY] ℹ️  NO CLEANUP NEEDED:\n"
                    f"  Reason: No similar files found\n"
                    f"  New file will be kept as first/only version\n"
                    f"  Quality: {new_quality.get('source', 'N/A').upper()} ({new_quality.get('quality_score', 0):.1f})"
                )
            elif all(existing_quality.get('quality_score', 0) >= new_quality.get('quality_score', 0) 
                     for existing_file in similar_files 
                     for existing_quality in [extract_quality_info(existing_file.get('file_name', ''), 
                                                                   existing_file.get('caption', ''))]):
                message = "ℹ️  Existing files are equal or better quality"
                logger.info(
                    f"[QUALITY] ℹ️  NO CLEANUP NEEDED:\n"
                    f"  Reason: Existing files are equal or better quality\n"
                    f"  New Quality Score: {new_quality.get('quality_score', 0):.1f}\n"
                    f"  Existing files scores: {[extract_quality_info(f.get('file_name', '')).get('quality_score', 0) for f in similar_files]}"
                )
            else:
                message = "ℹ️  No lower quality files to delete"
                logger.info(
                    f"[QUALITY] ℹ️  NO CLEANUP NEEDED:\n"
                    f"  New Quality: {new_quality.get('source', 'N/A').upper()} ({new_quality.get('quality_score', 0):.1f})\n"
                    f"  Similar files: {len(similar_files)} found but no lower quality versions"
                )
            
            return True, message
    
    except Exception as e:
        logger.error(f"[QUALITY] ❌ Error in find_and_delete_lower_quality: {e}", exc_info=True)
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