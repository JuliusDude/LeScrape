import json
import os
import subprocess
import time
import urllib.parse
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from ecourts.parser.search import parse_case_results
from ecourts.parser.case_details import parse_case_detail
from ecourts.storage.db import Database
from ecourts.export.exporter import Exporter

COOKIE_FILE = 'cookies.txt'
UA = 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36'
BASE_URL = 'https://services.ecourts.gov.in/ecourtindia_v6/'

def fetch_view_history(vp, current_token):
    payload = dict(vp)
    payload['ajax_req'] = 'true'
    payload['app_token'] = current_token

    encoded = urllib.parse.urlencode(payload)
    cmd = [
        'curl.exe', '-s', '-b', COOKIE_FILE, '-c', COOKIE_FILE,
        '-X', 'POST',
        '-H', f'User-Agent: {UA}',
        '-H', 'Accept: application/json, text/javascript, */*; q=0.01',
        '-H', 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8',
        '-H', 'Origin: https://services.ecourts.gov.in',
        '-H', f'Referer: {BASE_URL}?p=casestatus/index',
        '-H', 'X-Requested-With: XMLHttpRequest',
        '-H', 'delimeter: bnvfgffy6',
        '-H', 'Juihjrtr778: bnvfgffy6',
        '-d', encoded,
        BASE_URL + '?p=home/viewHistory'
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
    try:
        data = json.loads(res.stdout)
        new_token = data.get('app_token') or current_token
        html = data.get('data_list', '')
        return html, new_token
    except Exception as e:
        print(f'   [Error] Failed to parse viewHistory JSON: {e}')
        return '', current_token

def convert_to_excel(csv_path, excel_path):
    import csv
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Scraped Cases (100)'

    header_font = Font(name='Segoe UI', size=11, bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
    data_font = Font(name='Segoe UI', size=10)
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9'),
    )

    with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.reader(f)
        for r_idx, row in enumerate(reader, 1):
            ws.append(row)
            for c_idx in range(1, len(row) + 1):
                cell = ws.cell(row=r_idx, column=c_idx)
                cell.border = thin_border
                if r_idx == 1:
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                else:
                    cell.font = data_font
                    cell.alignment = Alignment(vertical='top', wrap_text=True)

    # Set column widths
    ws.row_dimensions[1].height = 28
    for col in ws.columns:
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    ws.freeze_panes = 'A2'
    wb.save(excel_path)
    print(f'[+] Excel workbook generated: {excel_path}')

def main():
    print('=' * 75)
    print(' eCourts India Live Scraper - 100 Cases Batch Execution')
    print('=' * 75)

    with open('search_result.json', 'r', encoding='utf-8') as f:
        search_data = json.load(f)

    app_token = search_data.get('app_token')
    cases = parse_case_results(search_data.get('case_data', ''))
    total_available = len(cases)
    target_count = min(100, total_available)

    print(f'[*] Total cases found from search: {total_available}')
    print(f'[*] Target to scrape deep details: {target_count}')
    print(f'[*] Destination SQLite Database: ecourts.db')
    print(f'[*] Inter-request delay: 2.0 seconds')
    print('-' * 75)

    db = Database('ecourts.db')

    start_time = time.time()
    successful = 0

    for i in range(target_count):
        case_item = cases[i]
        vp = case_item.get('view_params')
        case_num = case_item.get('case_number', f'Case #{i+1}')
        cnr = case_item.get('cnr_number') or (vp.get('cino') if vp else None)

        loop_start = time.time()
        print(f'[{i+1:3d}/{target_count}] Fetching details for {case_num} (CNR: {cnr})...', end='', flush=True)

        if vp:
            html, app_token = fetch_view_history(vp, app_token)
            if html:
                try:
                    cd = parse_case_detail(html)
                    d = cd.to_dict()
                    d.setdefault('court_name', case_item.get('court_name'))
                    d.setdefault('state_code', '3')
                    d.setdefault('dist_code', '20')
                    d.setdefault('court_complex_code', '1030136')
                    d.setdefault('est_code', '15')
                    db.save_case(d)
                    successful += 1
                    hearings_count = len(d.get('hearings', []))
                    stage = d.get('case_stage') or 'Unknown'
                    print(f' OK [Stage: {stage} | Hearings: {hearings_count}]')
                except Exception as ex:
                    print(f' PARSE ERROR: {ex}')
            else:
                print(' EMPTY RESPONSE')
        else:
            print(' NO VIEW PARAMS')

        # Enforce 2.0s pacing
        elapsed = time.time() - loop_start
        if elapsed < 2.0:
            time.sleep(2.0 - elapsed)

    total_time = time.time() - start_time
    print('-' * 75)
    print(f'[+] Scrape Completed!')
    print(f'    Successfully extracted & saved: {successful} / {target_count} cases')
    print(f'    Total Time Taken: {total_time:.2f} seconds ({total_time/60:.2f} minutes)')
    print(f'    Average Speed: {total_time/target_count:.2f} seconds per case')
    print('=' * 75)

    # Export to CSV
    csv_path = 'cases_100_export.csv'
    excel_path = 'cases_100_export.xlsx'

    exporter = Exporter(db)
    exporter.export(format='csv', mode='flat', output_path=csv_path)
    print(f'[+] Flat CSV exported: {csv_path}')

    convert_to_excel(csv_path, excel_path)
    db.close()

if __name__ == '__main__':
    main()
