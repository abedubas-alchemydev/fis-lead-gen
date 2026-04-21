from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pdfplumber


def _next_nonempty(lines: list[str], start_index: int) -> Optional[str]:
    for i in range(start_index + 1, len(lines)):
        line = lines[i].strip()
        if line:
            return line
    return None


def _prev_nonempty(lines: list[str], start_index: int) -> Optional[str]:
    for i in range(start_index - 1, -1, -1):
        line = lines[i].strip()
        if line:
            return line
    return None


def _normalize_amount_text(amount_text: str) -> str:
    # Fix OCR issue like "(5 l,404)" -> "(51,404)"
    s = amount_text.replace("l", "1").replace("I", "1")
    s = s.replace("$", "").replace(" ", "").replace(".", "")
    return s.strip()


def _amount_to_int(amount_text: str) -> int:
    s = _normalize_amount_text(amount_text)
    is_negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "")
    value = int(s)
    return -value if is_negative else value


def _clean_label(label: str) -> str:
    return (
        label.strip()
        .replace("Stocld10lders", "Stockholders")
        .replace("ST OCKHOLDERS'", "STOCKHOLDERS'")
    )


def _split_label_and_amount(line: str) -> Tuple[Optional[str], Optional[str]]:
    if line.startswith("Common Stock"):
        return None, None

    match = re.search(r'(?P<amount>\$?\s*\(?[.\d][\d,\sIl.]*\)?)\s*$', line)
    if not match:
        return None, None

    amount = match.group("amount")
    label = line[:match.start()].strip()

    if not label or not re.search(r'\d', amount):
        return None, None

    return _clean_label(label), amount


def _find_page_by_line(pdf: pdfplumber.PDF, target_line: str) -> str:
    target = target_line.strip().upper()
    for page in pdf.pages:
        text = page.extract_text() or ""
        lines = [line.strip().upper() for line in text.splitlines() if line.strip()]
        if any(line == target for line in lines):
            return text
    raise ValueError(f'Could not find page with line: {target_line}')


def extract_general_info(page_text: str) -> Dict[str, str]:
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    data: Dict[str, str] = {}

    for i, line in enumerate(lines):
        upper = line.upper()

        if "NAME" in upper and "FIRM" in upper:
            after_colon = line.split(":", 1)[1].strip() if ":" in line else ""
            if after_colon:
                data["Name of Firm"] = after_colon
            else:
                candidate = _prev_nonempty(lines, i) or _next_nonempty(lines, i)
                if candidate:
                    data["Name of Firm"] = candidate

        elif "ADDRESS OF PRINCIPAL PLACE OF BUSINESS" in upper:
            street = _next_nonempty(lines, i)
            city_state_zip = _next_nonempty(lines, i + 1)

            if street and street.startswith("("):
                street = _next_nonempty(lines, i + 1)
                city_state_zip = _next_nonempty(lines, i + 2)

            if city_state_zip and city_state_zip.startswith("("):
                city_state_zip = _next_nonempty(lines, i + 2)

            if street and city_state_zip:
                data["Address"] = f"{street}, {city_state_zip}"

        elif "PERSON TO CONTACT WITH REGARD TO THIS FILING" in upper:
            contact_line = _next_nonempty(lines, i)
            if contact_line and contact_line.startswith("("):
                contact_line = _next_nonempty(lines, i + 1)

            if contact_line:
                email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', contact_line)
                phone_match = re.search(
                    r'\(\d{3}\)\s*\d{3}-\d{4}|\d{3}[-.\s]\d{3}[-.\s]\d{4}',
                    contact_line,
                )

                email = email_match.group(0) if email_match else ""
                phone = phone_match.group(0) if phone_match else ""

                name = contact_line
                if email:
                    name = name.replace(email, "").strip()
                if phone:
                    name = name.replace(phone, "").strip()

                data["Person to contact with"] = name
                data["Phone number"] = phone
                data["Email"] = email

    return data


def extract_statement_of_financial_condition(page_text: str) -> Dict[str, Any]:
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]

    result: Dict[str, Any] = {
        "Title": "Statement of Financial Condition",
        "Date": None,
        "Assets": {},
        "Liabilities": {},
        "Stockholders' Equity": {},
    }

    section: Optional[str] = None

    for i, line in enumerate(lines):
        upper = line.upper()

        if upper == "STATEMENT OF FINANCIAL CONDITION":
            if i + 1 < len(lines):
                result["Date"] = lines[i + 1]
            continue

        if upper == "ASSETS":
            section = "Assets"
            continue

        if upper == "LIABILITIES":
            section = "Liabilities"
            continue

        if "STOCKHOLDERS" in upper and "EQUITY" in upper and "TOTAL" not in upper and "LIABILITIES AND" not in upper:
            section = "Stockholders' Equity"
            continue

        if upper == "LIABILITIES AND STOCKHOLDERS' EQUITY":
            continue

        if line.startswith("The accompanying notes"):
            break

        if section is None:
            continue

        if line.startswith("Common Stock"):
            result["Stockholders' Equity"]["Common Stock Description"] = line
            continue

        label, amount_text = _split_label_and_amount(line)
        if label and amount_text:
            result[section][label] = _amount_to_int(amount_text)

    return result


def extract_pdf_info(pdf_path: str | Path) -> Dict[str, Any]:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        registrant_page_text = _find_page_by_line(pdf, "A. REGISTRANT IDENTIFICATION")
        statement_page_text = _find_page_by_line(pdf, "STATEMENT OF FINANCIAL CONDITION")

    general_info = extract_general_info(registrant_page_text)
    statement = extract_statement_of_financial_condition(statement_page_text)

    return {
        **general_info,
        "Statement of financial condition": statement,
    }


if __name__ == "__main__":
    # Change this path to your PDF file
    pdf_file = r"D:\Deshorn Compliance Lead Generation Project\Web Application Deshorn\.tmp\pdf-cache\0000067037-000006703726000003.pdf"

    extracted = extract_pdf_info(pdf_file)
    print(json.dumps(extracted, indent=2))