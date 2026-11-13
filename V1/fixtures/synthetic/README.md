# Synthetic fixtures

Everything here is SYNTHETIC: hand-built from the structure of the real
`V1/fixtures/target_case_view.html`. These files are NOT portal evidence.
Each file states this in its first comment / `_comment` key. Exceptions: `search_record_not_found.json`
copies the real HAR response (entry 14 of `fixtures/services.ecourts.gov.in 2.har`).

| File | Purpose |
|---|---|
| sub_stage_in_status_cell.html | "Sub Stage" label inside the Case Status value cell (current parser yields `Case disposedSub Stage`) |
| sub_stage_with_value_in_cell.html | Same cell, plus a `Final decree` value |
| sub_stage_separate_row.html | Sub Stage as its own label/value row |
| sub_stage_four_cell_row.html | One row: Case Status / value / Sub Stage / value |
| two_order_tables.html | Two `order_table` tables under "Interim Orders" and "Final Orders / Judgements" |
| two_main_matters.html | Two Main Case No / Main Filing No pairs with CNR links |
| pending_next_date_stage.html | Pending case with Next Hearing Date, Case Stage, no disposal fields |
| no_cnr_row_with_main_cnr.html | No CNR row; only a Main Matters CNR link exists |
| multi_advocate.html | Petitioner with two advocate lines |
| masked_parties.html | Party names masked as `XXXXXXX` |
| ia_table.html | IA status table (UNVERIFIED markup, as expected by the base parser) |
| search_errormsg.json | Search response with `errormsg` "Invalid Captcha" and no `case_data` |
| search_record_not_found.json | Real "Record not found" `case_data` from the HAR |
| search_three_rows.json | Search result table with 3 rows, one court header, 9-arg `viewHistory` onclicks |
| viewhistory_ok.json | `{"data_list": <target_case_view.html>, "app_token": "tok2"}` |
| viewhistory_error.json | Error JSON without `data_list` |
