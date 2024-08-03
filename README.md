# AMEX Credit Card Transaction PDF Redactor

Whitelist keyword-based transactions from AMEX bills.

## Use case
You can maintain a whitelist of keywords you want to keep in your AMEX bill for reimbursement. Other transactions get redacted once you run the script on the PDF file.

## How to run
The app uses flask to run a webserver where you can supply keywords and the file you want to redact transactions from.
```
python app.py
```
