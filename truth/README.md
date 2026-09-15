# Ground truth

Hand-transcribed cells used to measure extraction accuracy. One file per PDF page,
named `<pdf stem>_p<page>_truth.csv`, columns `row,column,value`.

`row` is the printed row label. Nested labels are joined with ` / `, for example
`Mil / Superficie`. `column` is the printed column header. `value` is the number as
printed, without thousands separators.

`bulletin_3T25_p21_truth.csv`: 93 cells from table 03.01 of the INS Niger quarterly
bulletin, 3rd quarter 2025 (campagne agricole 2024/2025), transcribed from the rendered
page image, not from the PDF text layer. It covers 13 rows across 10 crops and all nine
columns, and deliberately includes the Poivron block where stage 1 fails.
