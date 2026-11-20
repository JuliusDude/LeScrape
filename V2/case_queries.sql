-- Current caseload, grouped by court and status.
SELECT court_name, case_status, COUNT(*) AS cases
FROM cases
GROUP BY court_name, case_status
ORDER BY court_name, case_status;

-- One row per case. Aggregate each child table before joining so multiple
-- hearings and orders on the same case do not multiply the totals.
WITH hearing_totals AS (
    SELECT cnr, COUNT(*) AS hearings, MAX(hearing_date_iso) AS latest_hearing
    FROM hearings
    GROUP BY cnr
), order_totals AS (
    SELECT cnr, COUNT(*) AS orders, SUM(fetched) AS downloaded_orders
    FROM orders
    GROUP BY cnr
)
SELECT c.cnr, c.case_number, c.case_status, h.latest_hearing,
       COALESCE(h.hearings, 0) AS hearings, COALESCE(o.orders, 0) AS orders,
       COALESCE(o.downloaded_orders, 0) AS downloaded_orders
FROM cases AS c
LEFT JOIN hearing_totals AS h ON h.cnr = c.cnr
LEFT JOIN order_totals AS o ON o.cnr = c.cnr
ORDER BY c.cnr;
