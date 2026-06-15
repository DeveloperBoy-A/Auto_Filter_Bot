import re
import logging
from typing import Optional, Tuple, List
from database.ia_filterdb import Media

logger = logging.getLogger(__name__)

# Quality hierarchy (lower number = lower quality, should be deleted first)
QUALITY_HIERARCHY = {
    # Theatre/Cam Quality (Lowest)
    "camrip": 1, "hdcam": 1, "hdtc": 2, "hdts": 2, "ts": 2, "tc": 2, "telesync": 2, "predvd": 3, "dvdscr": 3,
    # Standard Quality 
    "dvdrip": 4, "tvrip": 5, "hdtv": 5, 
    # Web Quality (Medium) 
    "webrip": 6, "web-dl": 7, "webdl": 7, "web dl": 7, 
    # Premium Quality (Highest) 
    "hdrip": 8, "bluray": 9, "bdrip": 9, "brrip": 9,
}

# Resolution quality mapping
RESOLUTION_HIERARCHY = {
    "240p": 1, "140p": 1, "360p": 2, "480p": 3, "540p": 4, "720p": 5, "1080p": 6, "1440p": 7, "2160p": 8, "4k": 8,
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
    text = f"{filename} {caption}".lower()
    quality_info = {
        'source': None, 'resolution': None, 'quality_score': 0, 'source_score': 0, 'resolution_score': 0
    }
    
    for source, score in QUALITY_HIERARCHY.items():
        pattern = rf'\b{re.escape(source)}\b'
        if re.search(pattern, text):
            quality_info['source'] = source
            quality_info['source_score'] = score
            break
            
    for res, score in RESOLUTION_HIERARCHY.items():
        pattern = rf'\b{re.escape(res)}\b'
        if re.search(pattern, text):
            quality_info['resolution'] = res
            quality_info['resolution_score'] = score
            break
            
    quality_info['quality_score'] = (quality_info['source_score'] * 0.7) + (quality_info['resolution_score'] * 0.3)
    return quality_info

def is_low_quality_print(quality_info: dict) -> bool:
    return quality_info.get('source') in LOW_QUALITY_SOURCES

def is_high_quality(quality_info: dict) -> bool:
    return quality_info.get('source') in HIGH_QUALITY_SOURCES

def should_delete_existing(existing_quality: dict, new_quality: dict, existing_lang: str, new_lang: str) -> bool:
    try:
        if existing_lang != new_lang and existing_lang != "unknown" and new_lang != "unknown":
            return False

        existing_source = existing_quality.get('source')
        new_source = new_quality.get('source')

        if not existing_source or not new_source:
            return False

        if existing_source in LOW_QUALITY_SOURCES and new_source in HIGH_QUALITY_SOURCES:
            return True

        return False
    except Exception as e:
        logger.error(f"Error in should_delete_existing: {e}")
        return False

def get_base_title(filename: str) -> str:
    text = filename.lower()
    text = re.sub(r'\.(mkv|mp4|avi|mov|wmv|flv|webm)$', '', text, flags=re.I)
    text = re.sub(r'[._-]+', ' ', text)

    for quality in list(QUALITY_HIERARCHY.keys()) + list(RESOLUTION_HIERARCHY.keys()):
        text = re.sub(rf'\b{re.escape(quality)}\b', '', text, flags=re.I)
        
    text = re.sub(
        r'\b(hevc|x265|x264|h264|avc|av1|aac|flac|dts|ac3|eac3|ddp|ddp5\.1|dd5\.1|5\.1|7\.1|2\.0|'
        r'dub|sub|esub|esubs|multi|proper|uncut|'
        r'hindi|english|tamil|telugu|malayalam|kannada|punjabi|'
        r'bengali|marathi|gujarati|movies4u|tokyo_updates|telly|amzn|nf|dsnp)\b', '', text, flags=re.I
    )
    text = re.sub(r'[\[\(\{].*?[\]\)\}]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

async def find_and_delete_lower_quality(
    db_collection, new_filename: str, new_caption: str = "", file_id: Optional[str] = None
) -> Tuple[bool, str]:
    try:
        if not new_filename or not isinstance(new_filename, str):
            logger.warning(f"[QUALITY] Invalid filename input: {new_filename}")
            return False, "Invalid filename provided"

        new_quality = extract_quality_info(new_filename, new_caption or "")
        
        logger.info(
            f"[QUALITY] New file uploaded:\n"
            f"  📄 Filename: {new_filename[:70]}\n"
            f"  🎬 Source: {new_quality.get('source', 'N/A').upper() if new_quality.get('source') else 'UNKNOWN'}\n"
            f"  📐 Resolution: {new_quality.get('resolution', 'N/A').upper() if new_quality.get('resolution') else 'UNKNOWN'}\n"
            f"  📊 Quality Score: {new_quality.get('quality_score', 0):.1f}"
        )

        if not new_quality['source']:
            logger.warning(f"[QUALITY] Could not detect quality source from filename: {new_filename}")
            return True, "No quality info found in new file, skipping cleanup"

        base_title = get_base_title(new_filename)
        if not base_title:
            logger.warning(f"[QUALITY] Could not extract base title from: {new_filename}")
            return True, "Could not extract title for comparison"

        logger.debug(f"[QUALITY] Base title extracted: {base_title}")

        try:
            words = [w for w in base_title.split() if len(w) > 1]
            pattern = ".*".join(map(re.escape, words[:5])) if words else re.escape(base_title)
        except Exception as e:
            logger.error(f"[QUALITY] Error building search pattern: {e}")
            return False, f"Error building search pattern: {str(e)}"
            
        search_query = {'file_name': {'$regex': pattern, '$options': 'i'}}
        if file_id:
            search_query['_id'] = {'$ne': file_id}

        try:
            similar_files = await db_collection.find(search_query).to_list(None)
        except Exception as e:
            logger.error(f"[QUALITY] Database query error: {e}")
            return False, f"Database error: {str(e)}"
            
        logger.info(f"[QUALITY] Similar files found in DB: {len(similar_files)}")
        
        deleted_count = 0
        deleted_files = []
        kept_files = []

        for existing_file in similar_files:
            try:
                existing_filename = existing_file.get('file_name', '')
                existing_caption = existing_file.get('caption', '')
                existing_quality = extract_quality_info(existing_filename, existing_caption or "")
                
                logger.debug(
                    f"[QUALITY] Checking existing file:\n"
                    f"  📄 {existing_filename[:60]}\n"
                    f"  🎬 Source: {existing_quality.get('source', 'N/A')}\n"
                    f"  📊 Score: {existing_quality.get('quality_score', 0):.1f}"
                )

                existing_lang = extract_language(f"{existing_filename} {existing_caption or ''}")
                new_lang = extract_language(f"{new_filename} {new_caption or ''}")
                
                if should_delete_existing(existing_quality, new_quality, existing_lang, new_lang):
                    try:
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
                        f"[QUALITY] ℹ️ Keeping file (equal or better quality):\n"
                        f"  📄 {existing_filename[:60]}\n"
                        f"  📊 Score: {existing_quality.get('quality_score', 0):.1f}"
                    )
            except Exception as e:
                logger.error(f"[QUALITY] Error processing file {existing_file.get('file_name', 'Unknown')}: {e}")
                continue

        if deleted_count > 0:
            message = (
                f"✅ Cleanup completed:\n"
                f" 🗑️ Deleted: {deleted_count} file(s)\n"
                f" ✨ Kept: {len(kept_files)} file(s)"
            )
            logger.warning(
                f"[QUALITY] ✅ CLEANUP SUMMARY:\n"
                f"  Movie: {base_title}\n"
                f"  🗑️ Deleted: {deleted_count} files\n"
                f"  ✨ Kept: {len(kept_files)} files\n"
                f"  New Quality: {new_quality.get('source', 'N/A').upper()} ({new_quality.get('quality_score', 0):.1f})"
            )
            return True, message
        else:
            if len(similar_files) == 0:
                message = "❌ No similar files found in database"
                logger.info(
                    f"[QUALITY] ℹ️ NO CLEANUP NEEDED:\n"
                    f"  Reason: No similar files found\n"
                    f"  New file will be kept as first/only version\n"
                    f"  Quality: {new_quality.get('source', 'N/A').upper()} ({new_quality.get('quality_score', 0):.1f})"
                )
            else:
                message = "ℹ️ No lower quality files to delete"
                logger.info(
                    f"[QUALITY] ℹ️ NO CLEANUP NEEDED:\n"
                    f"  New Quality: {new_quality.get('source', 'N/A').upper()} ({new_quality.get('quality_score', 0):.1f})\n"
                    f"  Similar files: {len(similar_files)} found but no lower quality versions"
                )
            return True, message

    except Exception as e:
        logger.error(f"[QUALITY] ❌ Error in find_and_delete_lower_quality: {e}", exc_info=True)
        return False, f"Error during quality cleanup: {str(e)}"

async def cleanup_duplicates(db_collection, base_title: str, keep_highest_quality: bool = True) -> Tuple[int, List[str]]:
    try:
        words = [w for w in base_title.split() if len(w) > 1]
        pattern = ".*".join(map(re.escape, words[:5])) if words else re.escape(base_title)
        
        search_query = {'file_name': {'$regex': pattern, '$options': 'i'}}
        files = await db_collection.find(search_query).to_list(None)
        
        if len(files) <= 1:
            return 0, []

        scored_files = []
        for file in files:
            quality = extract_quality_info(file.get('file_name', ''), file.get('caption', ''))
            scored_files.append({
                'file': file, 
                'quality': quality, 
                'score': quality['quality_score']
            })
            
        scored_files.sort(key=lambda x: x['score'], reverse=True)
        
        deleted_count = 0
        deleted_files = []

        if keep_highest_quality:
            has_high_quality = any(f['quality']['source'] in HIGH_QUALITY_SOURCES for f in scored_files)
            
            for scored_file in scored_files[1:]:
                file_source = scored_file['quality'].get('source')
                
                if file_source in LOW_QUALITY_SOURCES and has_high_quality:
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


# --- COMMAND HANDLERS ---

from pyrogram import Client, filters
from info import ADMINS
from collections import defaultdict

@Client.on_message(filters.command("quality_report") & filters.user(ADMINS))
async def quality_report_cmd(bot, message):
    try:
        msg = await message.reply_text("📊 Generating report...\n⏳ Please wait...")
        all_files = await Media.collection.find({}).to_list(None)
        if not all_files:
            return await msg.edit_text("❌ No files in database")
            
        logger.info(f"[QUALITY REPORT] Total files found: {len(all_files)}")
        quality_dist = defaultdict(int)
        resolution_dist = defaultdict(int)
        
        for file in all_files:
            file_name = file.get('file_name', '')
            quality_info = extract_quality_info(file_name)
            source = quality_info.get('source', 'unknown')
            resolution = quality_info.get('resolution', 'unknown')
            quality_dist[source] += 1
            resolution_dist[resolution] += 1

        report = f"📊 **QUALITY REPORT**\n{'='*50}\n\n📁 **Total Files:** {len(all_files)}\n\n🎬 **Source Quality Distribution:**\n{'─'*50}\n"
        quality_order = ['camrip', 'hdcam', 'hdtc', 'hdts', 'ts', 'tc', 'predvd', 'dvdscr', 'dvdrip', 'tvrip', 'hdtv', 'webrip', 'web-dl', 'webdl', 'hdrip', 'bluray', 'bdrip', 'brrip', 'unknown']
        
        for quality in quality_order:
            if quality in quality_dist and quality_dist[quality] > 0:
                count = quality_dist[quality]
                percent = (count / len(all_files) * 100) if len(all_files) > 0 else 0
                if quality in LOW_QUALITY_SOURCES: emoji = "⚠️ "
                elif quality in HIGH_QUALITY_SOURCES: emoji = "✨"
                else: emoji = "⭐"
                report += f"{emoji} {quality.upper()}: {count} ({percent:.1f}%)\n"

        report += "\n📐 **Resolution Distribution:**\n"
        report += "─" * 50 + "\n"
        for res in ['240p', '360p', '480p', '540p', '720p', '1080p', '1440p', '2160p', 'unknown']:
            if res in resolution_dist and resolution_dist[res] > 0:
                count = resolution_dist[res]
                percent = (count / len(all_files) * 100) if len(all_files) > 0 else 0
                report += f"📹 {res}: {count} ({percent:.1f}%)\n"

        low_count = sum(quality_dist.get(q, 0) for q in LOW_QUALITY_SOURCES)
        report += f"\n⚠️ **Low Quality Count:** {low_count} files\n"
        
        await msg.edit_text(report)
        logger.info(f"[QUALITY REPORT] Report generated - Low quality: {low_count}")
    except Exception as e:
        logger.error(f"[QUALITY REPORT] Error: {e}", exc_info=True)
        await message.reply_text(f"❌ Error: {str(e)}")


@Client.on_message(filters.command("cleanup_dry_single") & filters.user(ADMINS))
async def cleanup_dry_single_cmd(bot, message):
    try:
        if len(message.command) < 2:
            return await message.reply_text(
                "❌ Usage: /cleanup_dry_single <movie_name>\n\n"
                "Example: /cleanup_dry_single Oppenheimer 2023"
            )
            
        movie_name = " ".join(message.command[1:])
        msg = await message.reply_text(f"🔍 **DRY RUN - SINGLE**\n\nMovie: {movie_name}\n⏳ Scanning...")
        logger.info(f"[DRY RUN SINGLE] Starting for: {movie_name}")
        
        base_title = get_base_title(movie_name)
        logger.debug(f"[DRY RUN SINGLE] Base title: {base_title}")
        
        if not base_title:
            logger.warning(f"[DRY RUN SINGLE] Could not extract base title from: {movie_name}")
            return await msg.edit_text(f"❌ Could not extract title from: {movie_name}")

        words = [w for w in base_title.split() if len(w) > 1]
        search_pattern = ".*".join(map(re.escape, words[:5])) if words else re.escape(base_title)
        logger.debug(f"[DRY RUN SINGLE] Search pattern: {search_pattern}")

        try:
            similar_files = await Media.collection.find({'file_name': {'$regex': search_pattern, '$options': 'i'}}).to_list(None)
        except Exception as e:
            logger.error(f"[DRY RUN SINGLE] Database error: {e}")
            return await msg.edit_text(f"❌ Database error: {str(e)}")
            
        logger.info(f"[DRY RUN SINGLE] Found {len(similar_files)} files")
        
        if not similar_files:
            logger.warning(f"[DRY RUN SINGLE] No files found for: {base_title}")
            return await msg.edit_text(f"❌ No files found for: {movie_name}")

        files_info = []
        for file in similar_files:
            file_name = file.get('file_name', 'Unknown')
            quality = extract_quality_info(file_name)
            files_info.append({
                'name': file_name,
                'quality': quality['source'] or 'Unknown',
                'resolution': quality['resolution'] or 'Unknown',
                'score': quality['quality_score'],
                'id': str(file.get('_id', ''))
            })
            logger.debug(f"[DRY RUN SINGLE] File: {file_name[:50]} | Score: {quality['quality_score']:.1f}")

        files_info.sort(key=lambda x: x['score'], reverse=True)
        has_high_quality = any(f['quality'] in HIGH_QUALITY_SOURCES for f in files_info)

        report = f"📊 **DRY RUN - SINGLE MOVIE**\n{'='*50}\n\n🎬 Movie: {movie_name}\n📁 Found: {len(files_info)} files\n\n📋 **File Details:**\n{'─'*50}\n"
        
        to_delete = 0
        for idx, file in enumerate(files_info, 1):
            will_delete = False
            if idx > 1 and file['quality'] in LOW_QUALITY_SOURCES and has_high_quality:
                will_delete = True
                to_delete += 1
                
            status = "❌ DELETE (Low Quality)" if will_delete else "✅ KEEP"
            quality_str = file['quality'].upper() if file['quality'] != 'Unknown' else 'N/A'
            res_str = file['resolution'].upper() if file['resolution'] != 'Unknown' else 'N/A'
            
            report += f"\n{idx}. {status}\n  📄 {file['name'][:60]}\n  Quality: {quality_str} | Res: {res_str} | Score: {file['score']:.1f}\n"

        report += f"\n{'─'*50}\n⚠️ **PREVIEW:**\n✅ Will KEEP: {len(files_info) - to_delete}\n❌ Will DELETE: {to_delete}\n\n"
        
        if to_delete > 0:
            report += f"👉 Confirm & Delete:\n`/cleanup_confirm_single {movie_name}`"
        else:
            report += f"ℹ️ No files to delete (Rules applied)"
            
        await msg.edit_text(report)
        logger.info(f"[DRY RUN SINGLE] Complete - Will delete: {to_delete}")
    except Exception as e:
        logger.error(f"[DRY RUN SINGLE] Error: {e}", exc_info=True)
        await message.reply_text(f"❌ Error: {str(e)}")


@Client.on_message(filters.command("cleanup_confirm_single") & filters.user(ADMINS))
async def cleanup_confirm_single_cmd(bot, message):
    try:
        if len(message.command) < 2:
            return await message.reply_text(
                "❌ Usage: /cleanup_confirm_single <movie_name>\n\n"
                "⚠️ WARNING: Will PERMANENTLY DELETE files!\n\n"
                "Example: /cleanup_confirm_single Oppenheimer 2023"
            )
            
        movie_name = " ".join(message.command[1:])
        msg = await message.reply_text(
            f"⚠️ **CONFIRMING DELETE**\n\nMovie: {movie_name}\n🗑️ Processing...\n\n⏳ Please wait..."
        )
        logger.info(f"[CONFIRM SINGLE] Starting for: {movie_name}")
        
        base_title = get_base_title(movie_name)
        if not base_title:
            logger.error(f"[CONFIRM SINGLE] Could not extract title: {movie_name}")
            return await msg.edit_text(f"❌ Could not extract title from: {movie_name}")

        deleted_count, deleted_files = await cleanup_duplicates(db_collection=Media.collection, base_title=base_title, keep_highest_quality=True)
        logger.warning(f"[CONFIRM SINGLE] Movie: {movie_name} | Deleted: {deleted_count} files")
        
        if deleted_count > 0:
            deleted_preview = ""
            for idx, file in enumerate(deleted_files[:8], 1):
                deleted_preview += f"{idx}. {file[:55]}\n"
            if len(deleted_files) > 8:
                deleted_preview += f"... + {len(deleted_files) - 8} more\n"
                
            report = (
                f"✅ **DELETE COMPLETED!**\n{'='*50}\n\n"
                f"🎬 Movie: {movie_name}\n"
                f"🗑️ Deleted: {deleted_count} files (Low Quality)\n\n"
                f"📋 **Deleted Files:**\n{deleted_preview}"
            )
        else:
            report = (
                f"ℹ️ **No files deleted**\n\n"
                f"🎬 Movie: {movie_name}\n"
                f"Reason: No low-quality duplicates found based on strict rules."
            )
        await msg.edit_text(report)
    except Exception as e:
        logger.error(f"[CONFIRM SINGLE] Error: {e}", exc_info=True)
        await message.reply_text(f"❌ Error: {str(e)}")


@Client.on_message(filters.command("cleanup_dry_batch") & filters.user(ADMINS))
async def cleanup_dry_batch_cmd(bot, message):
    try:
        msg = await message.reply_text(f"🔍 **DRY RUN - BATCH MODE**\n\n⏳ Scanning all files...\n\n(DRY RUN - Nothing will be deleted)")
        logger.info(f"[DRY RUN BATCH] Starting")

        all_files = await Media.collection.find({}).to_list(None)
        logger.info(f"[DRY RUN BATCH] Total files in DB: {len(all_files)}")
        
        if not all_files:
            return await msg.edit_text("❌ No files in database")

        movies = defaultdict(list)
        for file in all_files:
            base_title = get_base_title(file.get('file_name', ''))
            if base_title:
                quality = extract_quality_info(file.get('file_name', ''))
                movies[base_title].append({
                    'name': file.get('file_name', ''),
                    'quality': quality['source'],
                    'resolution': quality['resolution'],
                    'score': quality['quality_score']
                })

        logger.info(f"[DRY RUN BATCH] Unique movies: {len(movies)}")

        total_to_delete = 0
        duplicate_movies = []
        
        for base_title, files in movies.items():
            if len(files) > 1:
                files.sort(key=lambda x: x['score'], reverse=True)
                has_high_quality = any(f['quality'] in HIGH_QUALITY_SOURCES for f in files)
                
                to_delete = sum(1 for f in files[1:] if f['quality'] in LOW_QUALITY_SOURCES and has_high_quality)
                
                if to_delete > 0:
                    total_to_delete += to_delete
                    duplicate_movies.append({
                        'title': base_title,
                        'count': len(files),
                        'to_delete': to_delete
                    })

        duplicate_movies.sort(key=lambda x: x['to_delete'], reverse=True)

        report = f"📊 **DRY RUN - BATCH MODE**\n{'='*50}\n\n"
        report += f"📁 Total Files: {len(all_files)}\n🎬 Total Movies: {len(movies)}\n"
        report += f"📋 Movies with Low-Q Duplicates: {len(duplicate_movies)}\n\n"
        report += f"⚠️ **WOULD DELETE: {total_to_delete} files**\n\n"

        if duplicate_movies:
            report += f"📋 **Top Movies with Duplicates:**\n{'─'*50}\n"
            for idx, movie in enumerate(duplicate_movies[:12], 1):
                report += f"{idx}. {movie['title'][:45]}\n   Versions: {movie['count']} | Will Delete: {movie['to_delete']}\n\n"
            if len(duplicate_movies) > 12:
                report += f"... + {len(duplicate_movies) - 12} more\n\n"
            report += f"{'─'*50}\n\n👉 Confirm & Delete ALL:\n`/cleanup_confirm_batch`"
            
        await msg.edit_text(report)
    except Exception as e:
        logger.error(f"[DRY RUN BATCH] Error: {e}", exc_info=True)
        await message.reply_text(f"❌ Error: {str(e)}")


@Client.on_message(filters.command("cleanup_confirm_batch") & filters.user(ADMINS))
async def cleanup_confirm_batch_cmd(bot, message):
    try:
        msg = await message.reply_text(f"⚠️ **CONFIRMING DELETE - BATCH**\n\n🗑️ Processing all files...\n\n⏳ This may take a while...")
        logger.info(f"[CONFIRM BATCH] Starting")

        all_files = await Media.collection.find({}).to_list(None)
        logger.info(f"[CONFIRM BATCH] Total files: {len(all_files)}")
        
        if not all_files:
            return await msg.edit_text("❌ No files in database")

        movies = defaultdict(list)
        for file in all_files:
            base_title = get_base_title(file.get('file_name', ''))
            if base_title:
                quality = extract_quality_info(file.get('file_name', ''))
                movies[base_title].append({
                    'file_obj': file,
                    'name': file.get('file_name', ''),
                    'quality': quality['source'],
                    'resolution': quality['resolution'],
                    'score': quality['quality_score']
                })

        total_deleted = 0
        movies_cleaned = 0
        deleted_files_list = []

        for base_title, files in movies.items():
            if len(files) > 1:
                files.sort(key=lambda x: x['score'], reverse=True)
                has_high_quality = any(f['quality'] in HIGH_QUALITY_SOURCES for f in files)
                
                cleaned_this_movie = False
                for file_data in files[1:]:
                    if file_data['quality'] in LOW_QUALITY_SOURCES and has_high_quality:
                        try:
                            await Media.collection.delete_one({'_id': file_data['file_obj']['_id']})
                            total_deleted += 1
                            deleted_files_list.append(file_data['name'])
                            cleaned_this_movie = True
                        except Exception as e:
                            logger.error(f"[CONFIRM BATCH] Delete error: {e}")
                if cleaned_this_movie:
                    movies_cleaned += 1

        logger.warning(f"[CONFIRM BATCH] Deleted: {total_deleted} files | Movies: {movies_cleaned}")

        if total_deleted > 0:
            deleted_preview = ""
            for idx, file in enumerate(deleted_files_list[:8], 1):
                deleted_preview += f"{idx}. {file[:55]}\n"
            if len(deleted_files_list) > 8:
                deleted_preview += f"... + {len(deleted_files_list) - 8} more\n"
                
            report = (
                f"✅ **BATCH DELETE COMPLETED!**\n{'='*50}\n\n"
                f"🗑️ Total Deleted: {total_deleted} files\n"
                f"🎬 Movies Cleaned: {movies_cleaned}\n\n"
                f"📋 **Sample Deleted:**\n{deleted_preview}"
            )
        else:
            report = f"ℹ️ **No files deleted**\n\nAll files are already optimal quality based on rules."
            
        await msg.edit_text(report)
    except Exception as e:
        logger.error(f"[CONFIRM BATCH] Error: {e}", exc_info=True)
        await message.reply_text(f"❌ Error: {str(e)}")
