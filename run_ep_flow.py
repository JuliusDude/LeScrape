import subprocess
import re
import json
import time
import urllib.parse
import csv
from bs4 import BeautifulSoup
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from ecourts.core.captcha import LocalOcrSolver
from ecourts.parser.search import parse_case_results
from ecourts.parser.case_details import parse_case_detail
from ecourts.storage.db import Database
from ecourts.export.exporter import export_to_csv_flat

COOKIE_JAR = 'cookies_ep_session.txt'
UA = 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36'
BASE = 'https://services.ecourts.gov.in/ecourtindia_v6/'

def run_curl(url, data=None, headers=None, is_binary=False):
    cmd = ['curl.exe', '-s', '-b', COOKIE_JAR, '-c', COOKIE_JAR]
    h = {'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9'}
    if headers:
        h.update(headers)
    for k, v in h.items():
        cmd.extend(['-H', f'{k}: {v}'])
    if data is not None:
        cmd.extend(['-X', 'POST'])
        cmd.extend(['-d', urllib.parse.urlencode(data)])
    cmd.append(url)
    res = subprocess.run(cmd, capture_output=True)
    if is_binary:
        return res.stdout
    return res.stdout.decode('utf-8', errors='ignore')

print('=' * 75)
print(' eCourts Execution Cases Scraper (20 Records Deep Extraction)')
print('=' * 75)

# 1. Base home & casestatus/index handshake
print('[1/5] Establishing session handshake...')
run_curl(BASE, headers={
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Upgrade-Insecure-Requests': '1'
})
time.sleep(2)

cs_html = run_curl(BASE + '?p=casestatus/index', headers={
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Referer': BASE,
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Upgrade-Insecure-Requests': '1'
})

m_token = re.search(r'id=["\']app_token["\'][^>]*value=["\']([^"\']+)', cs_html)
app_token = m_token.group(1) if m_token else ''

m_comp = re.search(r'src=["\']([^"\']*components\.js\?v=[^"\']*)', cs_html)
comp_url = m_comp.group(1) if m_comp else None
if comp_url and comp_url.startswith('/'):
    comp_full = 'https://services.ecourts.gov.in' + comp_url
else:
    comp_full = BASE + 'js/components.js'

time.sleep(2)
js_text = run_curl(comp_full, headers={'Referer': BASE + '?p=casestatus/index'})

m_del = re.search(r'var\s+delimeter\s*=\s*["\']([^"\']+)["\']', js_text)
delimeter = m_del.group(1) if m_del else 'uiryhjtry57'

m_hdr = re.search(r'headers\s*:\s*\{[^}]*"delimeter"\s*:\s*delimeter\s*,\s*["\']([^"\']+)["\']\s*:\s*delimeter', js_text)
custom_header = m_hdr.group(1) if m_hdr else None

print(f'      App Token   : {app_token[:16]}...')
print(f'      Delimiter   : {delimeter}')
print(f'      Custom Hdr  : {custom_header}')

ajax_headers = {
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
    'Origin': 'https://services.ecourts.gov.in',
    'Referer': BASE + '?p=casestatus/index',
    'X-Requested-With': 'XMLHttpRequest',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'delimeter': delimeter,
}
if custom_header:
    ajax_headers[custom_header] = delimeter

def post_api(endpoint, data):
    global app_token
    payload = {'ajax_req': 'true', 'app_token': app_token}
    payload.update(data)
    out = run_curl(BASE + '?p=' + endpoint, data=payload, headers=ajax_headers)
    try:
        j = json.loads(out)
        if isinstance(j, dict) and j.get('app_token'):
            app_token = j['app_token']
        return j
    except Exception as e:
        print(f'      [!] Error parsing {endpoint}: {e}')
        return {}

# 2. Sync Court Complex & Establishment
print('[2/5] Synchronizing Court Establishment (NyayaDegula complex 1030136, est 15)...')
time.sleep(2)
post_api('casestatus/set_data', {
    'complex_code': '1030136@15,16,17,18,19,20,21,23@Y',
    'selected_state_code': '3',
    'selected_dist_code': '20',
    'selected_est_code': '15',
})

solver = LocalOcrSolver()

def fetch_and_solve_captcha():
    time.sleep(2)
    cap_resp = post_api('casestatus/getCaptcha', {})
    div_cap = cap_resp.get('div_captcha', '')
    soup = BeautifulSoup(div_cap, 'html.parser')
    img_tag = soup.find('img')
    if not img_tag or not img_tag.get('src'):
        return None
    src = img_tag['src'].replace('\\/', '/').replace('\\', '')
    if src.startswith('/'):
        src = 'https://services.ecourts.gov.in' + src
    img_bytes = run_curl(src, headers={'Referer': BASE + '?p=casestatus/index'}, is_binary=True)
    return solver.solve(img_bytes)

# 3. Search Execution Cases (25^15, Pending, 2023)
print('[3/5] Querying Execution Cases (Type: Ex.C 25^15, Status: Pending, Year: 2023)...')
cases = []
for attempt in range(5):
    sol = fetch_and_solve_captcha()
    if not sol:
        print('      [!] CAPTCHA solve returned empty, retrying...')
        continue
    print(f'      CAPTCHA Solved: "{sol}". Submitting query...')
    time.sleep(2)
    s_payload = {
        'case_type_1': '25^15',
        'search_year': '2023',
        'case_status': 'Pending',
        'ct_captcha_code': sol,
        'state_code': '3',
        'dist_code': '20',
        'court_complex_code': '1030136',
        'est_code': '15',
    }
    res_s = post_api('casestatus/submit_case_type', s_payload)
    case_html = res_s.get('case_data', '')
    if 'Invalid Captcha' in case_html or 'Enter Captcha' in case_html:
        print('      [!] Captcha rejected, retrying...')
        continue
    cases = parse_case_results(case_html)
    print(f'      [+] Query succeeded: {len(cases)} cases retrieved.')
    if cases:
        break

if not cases:
    print('[-] Failed to retrieve cases after retries.')
    exit(1)

target_cases = cases[:20]
print(f'[4/5] Scraping full records for top {len(target_cases)} Execution cases...')

db = Database('ecourts.db')
saved_cnrs = []

for idx, c in enumerate(target_cases, 1):
    vp = c.get('view_params')
    case_no = c.get('case_number', f'Case #{idx}')
    cnr_val = c.get('cnr_number') or (vp.get('cino') if vp else 'N/A')
    t0 = time.time()
    print(f'      [{idx:2d}/{len(target_cases)}] Scraping {case_no} ({cnr_val})... ', end='', flush=True)

    if vp:
        vp_copy = dict(vp)
        vp_copy['ajax_req'] = 'true'
        vp_copy['app_token'] = app_token
        time.sleep(max(0.0, 2.0 - (time.time() - t0)))
        out_h = run_curl(BASE + '?p=home/viewHistory', data=vp_copy, headers=ajax_headers)
        try:
            h_json = json.loads(out_h)
            if h_json.get('app_token'):
                app_token = h_json['app_token']
            detail_html = h_json.get('data_list', '')
            parsed_detail = parse_case_detail(detail_html)
            d_dict = parsed_detail.to_dict()
            d_dict.setdefault('court_name', c.get('court_name'))
            d_dict.setdefault('state_code', '3')
            d_dict.setdefault('dist_code', '20')
            d_dict.setdefault('court_complex_code', '1030136')
            d_dict.setdefault('est_code', '15')
            db.save_case(d_dict)
            cnr = d_dict.get('cnr_number')
            if cnr:
                saved_cnrs.append(cnr)
            stage = d_dict.get('case_stage', 'N/A')
            hearings_cnt = len(d_dict.get('hearings', []))
            parties_cnt = len(d_dict.get('parties', []))
            print(f'OK [Stage: {stage} | Parties: {parties_cnt} | Hearings: {hearings_cnt}]')
        except Exception as err:
            print(f'ERR: {err}')
    else:
        print('SKIPPED')

print('-' * 75)
print(f'[+] Persisted {len(saved_cnrs)} Execution cases to ecourts.db.')

# 5. Export dataset
print('[5/5] Exporting dataset to Excel and CSV...')
csv_file = 'execution_petitions_20.csv'
excel_file = 'execution_petitions_20.xlsx'
export_to_csv_flat(db, csv_file, cnr_list=saved_cnrs)
print(f'      [+] Flat CSV exported: {csv_file}')

wb = openpyxl.Workbook()
ws = wb.active
ws.title = 'Execution Cases (20)'

header_font = Font(name='Segoe UI', size=11, bold=True, color='FFFFFF')
header_fill = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
data_font = Font(name='Segoe UI', size=10)
border_thin = Border(
    left=Side(style='thin', color='D9D9D9'),
    right=Side(style='thin', color='D9D9D9'),
    top=Side(style='thin', color='D9D9D9'),
    bottom=Side(style='thin', color='D9D9D9')
)

with open(csv_file, 'r', encoding='utf-8', errors='ignore') as f:
    reader = csv.reader(f)
    for r_idx, row in enumerate(reader, 1):
        ws.append(row)
        for c_idx in range(1, len(row) + 1):
            cell = ws.cell(row=r_idx, column=c_idx)
            cell.border = border_thin
            if r_idx == 1:
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            else:
                cell.font = data_font
                cell.alignment = Alignment(vertical='top', wrap_text=True)

ws.row_dimensions[1].height = 28
for col in ws.columns:
    col_letter = openpyxl.utils.get_column_letter(col[0].column)
    max_len = max(len(str(cell.value or '')) for cell in col)
    ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

ws.freeze_panes = 'A2'
wb.save(excel_file)
print(f'      [+] Styled Excel workbook generated: {excel_file}')

db.close()
print('=' * 75)
print(' Done! All 20 Execution Cases scraped and exported successfully.')
print('=' * 75)
