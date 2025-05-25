import requests # همچنان برای برخی کارها ممکن است لازم باشد
from bs4 import BeautifulSoup
import re
import json
import os
from packaging.version import parse, InvalidVersion
from urllib.parse import urljoin, urlparse, unquote
import logging
import time
import sys # برای sys.exit(1)

# ایمپورت های Selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager # برای مدیریت آسان درایور کروم
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By


# --- پیکربندی اولیه ---
URL_FILE = "urls_to_check.txt"
TRACKING_FILE = "versions_tracker.json"
OUTPUT_JSON_FILE = "updates_found.json"
GITHUB_OUTPUT_FILE = os.environ.get('GITHUB_OUTPUT', 'local_github_output.txt') # برای خروجی تعداد

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

# --- توابع کمکی ---

def load_tracker():
    """فایل ردیابی نسخه ها را بارگذاری می کند."""
    if os.path.exists(TRACKING_FILE):
        try:
            with open(TRACKING_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logging.info(f"فایل ردیابی {TRACKING_FILE} با موفقیت بارگذاری شد.")
                return data
        except json.JSONDecodeError:
            logging.warning(f"{TRACKING_FILE} خراب است. با ردیاب خالی شروع می شود.")
            return {}
    logging.info(f"فایل ردیابی {TRACKING_FILE} یافت نشد. با ردیاب خالی شروع می شود.")
    return {}

def compare_versions(current_v_str, last_v_str):
    """نسخه فعلی را با آخرین نسخه شناخته شده مقایسه می کند."""
    logging.info(f"مقایسه نسخه ها: فعلی='{current_v_str}', قبلی='{last_v_str}'")
    try:
        if not current_v_str:
            logging.warning("نسخه فعلی نامعتبر است (خالی).")
            return False

        if not last_v_str or last_v_str == "0.0.0":
            logging.info("نسخه قبلی یافت نشد یا 0.0.0 بود، نسخه فعلی جدید است.")
            return True

        normalize_for_parse = lambda v: re.split(r'[^0-9.]', v, 1)[0].strip('.')
        
        current_norm = normalize_for_parse(current_v_str)
        last_norm = normalize_for_parse(last_v_str)

        if not current_norm:
            logging.warning(f"نسخه فعلی '{current_v_str}' پس از نرمال سازی نامعتبر شد ('{current_norm}').")
            return False
        if not last_norm:
            logging.warning(f"نسخه قبلی '{last_v_str}' پس از نرمال سازی نامعتبر شد ('{last_norm}').")
            return True

        parsed_current = parse(current_norm)
        parsed_last = parse(last_norm)
        is_newer = parsed_current > parsed_last
        logging.info(f"نتیجه مقایسه (تجزیه شده): فعلی='{parsed_current}', قبلی='{parsed_last}', جدیدتر: {is_newer}")
        return is_newer
    except InvalidVersion as e:
        logging.warning(f"خطای InvalidVersion هنگام مقایسه '{current_v_str}' با '{last_v_str}': {e}. مقایسه به صورت رشته ای انجام می شود.")
        return current_v_str != last_v_str
    except Exception as e:
        logging.error(f"خطای پیش بینی نشده هنگام مقایسه نسخه ها: {e}. مقایسه به صورت رشته ای انجام می شود.")
        return current_v_str != last_v_str

def sanitize_text(text, for_filename=False):
    """متن را پاکسازی می کند."""
    if not text: return ""
    text = text.strip()

    text = re.sub(r'\((farsroid\.com|.*?)\)', '', text, flags=re.IGNORECASE).strip()
    
    if for_filename:
        text = text.lower()
        text = text.replace('–', '-').replace('—', '-')
        text = re.sub(r'[<>:"/\\|?*()]', '_', text) # پرانتزها با آندرلاین، براکت‌ها باقی می‌مانند
        text = re.sub(r'\s+', '_', text)
        text = text.replace('-_', '_') 
        text = text.replace('_-', '_')
        text = re.sub(r'_+', '_', text)
        text = text.strip('_')
    else: # for tracking_id
        text = text.lower()
        text = text.replace('–', '-').replace('—', '-')
        text = re.sub(r'[\(\)\[\]]', '', text) # حذف پرانتز و براکت برای شناسه
        text = re.sub(r'\s+', '_', text)
        text = text.strip('_')
    return text

def extract_app_name_from_page(soup, page_url):
    """تلاش برای استخراج نام برنامه از صفحه."""
    app_name_candidate = None

    h1_tag = soup.find('h1', class_=re.compile(r'title', re.IGNORECASE))
    if h1_tag:
        app_name_candidate = h1_tag.text.strip()

    if not app_name_candidate:
        title_tag = soup.find('title')
        if title_tag:
            app_name_candidate = title_tag.text.strip()
            app_name_candidate = re.sub(r'\s*([-|–])\s*(فارسروید|دانلود.*)$', '', app_name_candidate, flags=re.IGNORECASE).strip()
            app_name_candidate = re.sub(r'\s*–\s*اپلیکیشن.*$', '', app_name_candidate, flags=re.IGNORECASE).strip()

    if app_name_candidate:
        if app_name_candidate.lower().startswith("دانلود "):
            app_name_candidate = app_name_candidate[len("دانلود "):].strip()
        return app_name_candidate

    logging.info(f"نام برنامه از H1 یا Title استخراج نشد، تلاش برای استخراج از URL: {page_url}")
    parsed_url = urlparse(page_url)
    path_parts = [part for part in parsed_url.path.split('/') if part]
    if path_parts:
        guessed_name = path_parts[-1].replace('-', ' ').replace('_', ' ')
        guessed_name = re.sub(r'\.(html|php|asp|aspx)$', '', guessed_name, flags=re.IGNORECASE).strip()
        guessed_name = re.sub(r'^(دانلود|برنامه)\s+', '', guessed_name, flags=re.IGNORECASE).strip()
        guessed_name = re.sub(r'\s+(?:apk|android|ios|mod|hack|premium|pro|full|unlocked|final|update)$', '', guessed_name, flags=re.IGNORECASE).strip()
        logging.info(f"نام حدس زده شده از URL: {guessed_name.title()}")
        return guessed_name.title()
        
    logging.warning(f"نام برنامه از هیچ منبعی استخراج نشد. URL: {page_url}")
    return "UnknownApp"


def get_page_source_with_selenium(url, wait_time=20, wait_for_class="downloadbox"):
    """صفحه را با Selenium بارگذاری کرده و سورس HTML آن را پس از اجرای JS برمی گرداند."""
    logging.info(f"در حال دریافت {url} با Selenium...")
    chrome_options = ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36")

    driver = None
    try:
        try:
            driver_path = ChromeDriverManager().install()
            service = ChromeService(executable_path=driver_path)
            logging.info(f"ChromeDriverManager در مسیر '{driver_path}' پیدا/نصب شد.")
        except Exception as e_driver_manager:
            logging.warning(f"خطا در استفاده از ChromeDriverManager: {e_driver_manager}. تلاش برای استفاده از درایور پیشفرض سیستم.")
            service = ChromeService()

        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(url)
        
        logging.info(f"منتظر بارگذاری محتوای دینامیک (تا {wait_time} ثانیه) برای کلاس '{wait_for_class}'...")
        try:
            WebDriverWait(driver, wait_time).until(
                EC.presence_of_element_located((By.CLASS_NAME, wait_for_class))
            )
            time.sleep(7)
            logging.info(f"عنصر با کلاس '{wait_for_class}' پیدا شد و زمان اضافی برای بارگذاری داده شد.")
        except Exception as e_wait:
            logging.warning(f"Timeout یا خطا هنگام انتظار برای '{wait_for_class}': {e_wait}. ممکن است صفحه کامل بارگذاری نشده باشد.")
            if driver: return driver.page_source
            return None

        page_source = driver.page_source
        logging.info(f"موفقیت در دریافت سورس صفحه با Selenium برای {url}")
        return page_source
    except Exception as e:
        logging.error(f"خطای Selenium هنگام دریافت {url}: {e}", exc_info=True)
        return None
    finally:
        if driver:
            driver.quit()
            logging.info("Selenium WebDriver بسته شد.")

# --- منطق خراش دادن خاص سایت فارسروید ---
def scrape_farsroid_page(page_url, soup, tracker_data):
    updates_found_on_page = []
    # page_app_name شامل نام کامل و توصیفی برنامه استخراج شده از صفحه است
    page_app_name = extract_app_name_from_page(soup, page_url)
    logging.info(f"پردازش صفحه فارسروید: {page_url} (نام برنامه استخراج شده: '{page_app_name}')")

    download_box = soup.find('section', class_='downloadbox')
    if not download_box:
        logging.warning(f"باکس دانلود در {page_url} پیدا نشد.")
        return updates_found_on_page
    logging.info("باکس دانلود پیدا شد.")

    download_links_ul = download_box.find('ul', class_='download-links')
    if not download_links_ul:
        logging.warning(f"لیست لینک های دانلود (ul.download-links) در {page_url} پیدا نشد.")
        logging.info(f"محتوای HTML باکس دانلود (اگر ul پیدا نشد):\n{download_box.prettify()[:2000]}")
        return updates_found_on_page
    logging.info("لیست لینک های دانلود (ul.download-links) پیدا شد.")
    logging.debug(f"محتوای کامل HTML تگ ul.download-links:\n{download_links_ul.prettify()}")

    found_lis = download_links_ul.find_all('li', class_='download-link')
    logging.info(f"تعداد {len(found_lis)} آیتم li.download-link پیدا شد.")

    if not found_lis:
        logging.warning("هیچ آیتم li.download-link پیدا نشد.")
        return updates_found_on_page

    for i, li in enumerate(found_lis):
        logging.info(f"--- پردازش li شماره {i+1} ---")
        link_tag = li.find('a', class_='download-btn')
        if not link_tag:
            logging.warning(f"  تگ a.download-btn در li شماره {i+1} پیدا نشد. رد شدن...")
            continue

        download_url = link_tag.get('href')
        if not download_url:
             logging.warning(f"  تگ a.download-btn در li شماره {i+1} فاقد href است. رد شدن...")
             continue
        download_url = urljoin(page_url, download_url)

        link_text_span = link_tag.find('span', class_='txt')
        link_text = link_text_span.text.strip() if link_text_span else "متن لینک یافت نشد"

        if link_text == "متن لینک یافت نشد":
            logging.warning(f"  متن لینک در li شماره {i+1} یافت نشد. رد شدن...")
            continue

        logging.info(f"  URL: {download_url}")
        logging.info(f"  متن: {link_text}")

        version_match_url = re.search(r'(\d+\.\d+(?:\.\d+){0,2}(?:[.-][a-zA-Z0-9]+)*)', download_url)
        current_version_candidate_url = version_match_url.group(1) if version_match_url else None
        version_match_text = re.search(r'(\d+\.\d+(?:\.\d+){0,2}(?:[.-][a-zA-Z0-9]+)*)', link_text)
        current_version_candidate_text = version_match_text.group(1) if version_match_text else None
        current_version = current_version_candidate_url or current_version_candidate_text

        if not current_version:
            version_match_v_url = re.search(r'[vV](\d+\.\d+(?:\.\d+){0,2}(?:[.-][a-zA-Z0-9]+)*)', download_url)
            if version_match_v_url : current_version = version_match_v_url.group(1)
            if not current_version:
                version_match_v_text = re.search(r'[vV](\d+\.\d+(?:\.\d+){0,2}(?:[.-][a-zA-Z0-9]+)*)', link_text)
                if version_match_v_text: current_version = version_match_v_text.group(1)

        if not current_version:
            logging.warning(f"  نسخه از URL '{download_url}' یا متن '{link_text}' استخراج نشد. رد شدن...")
            continue
        logging.info(f"  نسخه استخراج شده: {current_version}")

        variant = "Unknown" 
        filename_in_url_lower = unquote(urlparse(download_url).path.split('/')[-1]).lower()
        link_text_lower = link_text.lower()
        combined_text_for_variant = filename_in_url_lower + " " + link_text_lower

        if 'premium' in combined_text_for_variant or 'پرمیوم' in link_text_lower:
            variant = "Premium"
        elif 'mod' in combined_text_for_variant or 'مود' in link_text_lower:
            variant = "Mod"
        elif 'lite' in combined_text_for_variant or 'لایت' in link_text_lower:
            variant = "Lite"
        elif 'arm64-v8a' in combined_text_for_variant or 'arm64' in combined_text_for_variant : 
            variant = "Arm64-v8a"
        elif 'armeabi-v7a' in combined_text_for_variant or 'armv7' in combined_text_for_variant : 
            variant = "Armeabi-v7a"
        elif 'x86_64' in combined_text_for_variant: 
            variant = "x86_64"
        elif 'x86' in combined_text_for_variant: 
            variant = "x86"
        elif 'universal' in combined_text_for_variant or 'اصلی' in link_text_lower or 'original' in combined_text_for_variant:
            variant = "Universal"
        
        if download_url.endswith(".zip"):
            if "windows" in combined_text_for_variant or "ویندوز" in link_text_lower :
                 variant = "Windows"
            elif variant == "Unknown": # اگر همچنان ناشناخته بود و zip بود
                 variant = "Data" 
        elif download_url.endswith(".apk") and variant == "Unknown":
            variant = "Universal" # پیشفرض برای APK اگر هیچ نوع دیگری تشخیص داده نشود

        logging.info(f"  نوع (Variant) نهایی: {variant}")

        # --- ساخت شناسه ردیابی ---
        # page_app_name نام کاملتر استخراج شده از H1/Title است.
        # sanitize_text با for_filename=False برای tracking_id استفاده می‌شود تا براکت‌ها و ... حذف شوند.
        tracking_id_base_name = sanitize_text(page_app_name, for_filename=False) 
        tracking_id_variant = sanitize_text(variant, for_filename=False)
        tracking_id = f"{tracking_id_base_name}_{tracking_id_variant}".lower()
        
        last_known_version = tracker_data.get(tracking_id, "0.0.0")

        if compare_versions(current_version, last_known_version):
            logging.info(f"    => آپدیت جدید برای {tracking_id}: {current_version} (قبلی: {last_known_version})")
            
            # --- ساخت نام فایل پیشنهادی ---
            # برای نام فایل، از یک نسخه ساده شده از page_app_name استفاده می‌کنیم (مثلا فقط اولین بخش قبل از – یا -)
            # این کار برای هماهنگی با نحوه نامگذاری فایل‌ها توسط اسکریپت دانلودر در GitHub Actions است.
            simple_app_name_base = page_app_name.split('–')[0].strip()
            if len(simple_app_name_base) > 30 or not simple_app_name_base : # اگر با – جدا نشد یا خیلی طولانی بود
                simple_app_name_base = page_app_name.split('-')[0].strip()
            if len(simple_app_name_base) > 30 or not simple_app_name_base : # اگر باز هم جدا نشد یا خیلی طولانی بود
                simple_app_name_base = page_app_name.split(' ')[0].strip()
            
            app_name_for_file = sanitize_text(simple_app_name_base, for_filename=True)
            variant_for_file = sanitize_text(variant, for_filename=True)
            
            file_extension = ".zip" if download_url.endswith(".zip") else ".apk"
            suggested_filename = f"{app_name_for_file}_v{current_version}_{variant_for_file}{file_extension}"
            
            updates_found_on_page.append({
                "app_name": page_app_name, # نام برنامه کامل و توصیفی برای نمایش در JSON
                "version": current_version,
                "variant": variant, # variant دقیق‌تر (مثلاً Premium, Universal, Windows)
                "download_url": download_url,
                "page_url": page_url,
                "tracking_id": tracking_id,
                "suggested_filename": suggested_filename, # نام فایل هماهنگ شده با دانلودر
                "current_version_for_tracking": current_version
            })
        else:
            logging.info(f"    => {tracking_id} به‌روز است (فعلی: {current_version}, قبلی: {last_known_version}).")
    return updates_found_on_page

# --- منطق اصلی ---
def main():
    if not os.path.exists(URL_FILE):
        logging.error(f"فایل URL ها یافت نشد: {URL_FILE}")
        with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f: json.dump([], f)
        if 'GITHUB_OUTPUT' in os.environ:
            with open(GITHUB_OUTPUT_FILE, 'a') as gh_output: gh_output.write(f"updates_count=0\n")
        sys.exit(1)

    with open(URL_FILE, 'r', encoding='utf-8') as f:
        urls_to_process = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    if not urls_to_process:
        logging.info("فایل URL ها خالی است.")
        with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f: json.dump([], f)
        if 'GITHUB_OUTPUT' in os.environ:
            with open(GITHUB_OUTPUT_FILE, 'a') as gh_output: gh_output.write(f"updates_count=0\n")
        return

    tracker_data = load_tracker()
    all_updates_found = []
    new_tracker_data = tracker_data.copy() 

    for page_url in urls_to_process:
        logging.info(f"\n--- شروع بررسی URL: {page_url} ---")
        page_content = get_page_source_with_selenium(page_url, wait_for_class="downloadbox")
        
        if not page_content:
            logging.error(f"محتوای صفحه برای {page_url} با Selenium دریافت نشد. رد شدن...")
            continue
        
        try:
            soup = BeautifulSoup(page_content, 'html.parser')
            if "farsroid.com" in page_url.lower():
                updates_on_page = scrape_farsroid_page(page_url, soup, tracker_data)
                all_updates_found.extend(updates_on_page)
                for update_info in updates_on_page:
                    new_tracker_data[update_info["tracking_id"]] = update_info["current_version_for_tracking"]
            else:
                logging.warning(f"خراش دهنده برای {page_url} پیاده سازی نشده است.")
        except Exception as e:
            logging.error(f"خطا هنگام پردازش محتوای دریافت شده از Selenium برای {page_url}: {e}", exc_info=True)
        logging.info(f"--- پایان بررسی URL: {page_url} ---")

    with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f:
        output_for_downloader = []
        for item in all_updates_found:
            output_for_downloader.append({
                "app_name": item["app_name"],
                "version": item["version"],
                "variant": item["variant"], # این variant باید توسط دانلودر استفاده شود
                "download_url": item["download_url"],
                "page_url": item["page_url"],
                "suggested_filename": item["suggested_filename"] 
            })
        json.dump(output_for_downloader, f, ensure_ascii=False, indent=2)

    try:
        with open(TRACKING_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_tracker_data, f, ensure_ascii=False, indent=2)
        logging.info(f"فایل ردیاب {TRACKING_FILE} با موفقیت بروزرسانی شد.")
    except Exception as e:
        logging.error(f"خطا در ذخیره فایل ردیاب {TRACKING_FILE}: {e}")

    num_updates = len(all_updates_found)
    if 'GITHUB_OUTPUT' in os.environ:
        with open(GITHUB_OUTPUT_FILE, 'a') as gh_output:
            gh_output.write(f"updates_count={num_updates}\n")

    logging.info(f"\nخلاصه: {num_updates} آپدیت پیدا شد. جزئیات در {OUTPUT_JSON_FILE}")

if __name__ == "__main__":
    main()
