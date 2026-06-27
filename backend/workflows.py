"""
Qatar e-Services Workflow Knowledge Base (Merab).

The planner retrieves the matching workflow by `intent` BEFORE driving the browser.
Rule of thumb: navigate DIRECTLY to the deep-link `url` instead of clicking through
menus. Only the listed clicks use exact visible `label` text.

This module is the single source of truth for the KB. It is plain Python (no YAML
dependency) so it always imports cleanly. The agent uses it to:
  • match a user message to a concrete task (match_task),
  • know exactly which inputs the task needs and where they come from (INPUT_SPECS),
  • build a strict, authoritative "playbook" to inject into the planner (build_playbook),
  • resolve the deterministic deep-link URL to open first (resolve_url).

Design goal: STRICT, low-hallucination, low-crash. Code opens the exact URL and
fills known values; the model only follows the labelled steps and pauses for the
human at the gates (captcha / OTP / any write).
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# Global rules (human-in-the-loop gates + dry-run policy)
# --------------------------------------------------------------------------- #
GLOBAL_RULES: dict[str, Any] = {
    "auth": {
        "on_auth_required": "confirm_with_user",   # ask before: log in or register?
        "credentials_input": "modal",              # collect username + password in a modal
        "never_in_modal": ["captcha", "otp"],      # solved by the human in the visible browser
    },
    "gates": {                                     # human-in-the-loop (pause_for_user)
        "captcha": "human_solves_in_browser",
        "otp": "human_solves_in_browser",
    },
    "dry_run": {
        # read-only actions run freely; these write actions pause for review first
        "block_before": ["submit_write", "send", "pay", "final_confirm"],
    },
}

# --------------------------------------------------------------------------- #
# Input specifications: maps a workflow input key -> the profile field it comes
# from, a human label (for the "please provide" modal), and an optional date format.
# --------------------------------------------------------------------------- #
INPUT_SPECS: dict[str, dict[str, Any]] = {
    "qid": {
        "profile": "qid",
        "label": "Qatar ID (QID)",
        "placeholder": "e.g. 28xxxxxxxxxx",
        "type": "text",
        # Synonyms used to find the field on the page deterministically.
        "match": ["qid", "id number", "personal number", "civil id", "national id", "qatar id"],
    },
    "dob": {
        "profile": "dob",
        "label": "Date of Birth",
        "format": "yyyy/mm/dd",
        "type": "date",
        "match": ["date of birth", "dob", "birth date", "birthdate"],
    },
    "residence_expiry_date": {
        "profile": "residence_expiry",
        "label": "Residence Expiry Date",
        "format": "yyyy/mm/dd",
        "type": "date",
        "match": ["residence expiry",
                  "residence permit expiry", "residence expiry date", "expiry date"],
    },
    "email": {
        "profile": "email",
        "label": "Email",
        "placeholder": "you@example.com",
        "type": "text",
        "match": ["email", "e-mail", "mail"],
    },
}


# --------------------------------------------------------------------------- #
# Services & tasks. Each task carries the data the agent needs to act strictly.
#   intents     : phrases that route a user message to this task
#   url         : the deep link to open FIRST (deterministic); may contain {patientID}
#   inputs      : ordered input keys (see INPUT_SPECS)
#   steps       : ordered, human-readable instructions with EXACT labels
#   read_only   : False => a write action; a dry-run confirm precedes the submit
#   requires_login / auth : whether the service needs sign-in
#   gates       : which human gates appear (captcha / otp)
# --------------------------------------------------------------------------- #
SERVICES: dict[str, dict[str, Any]] = {
    "phcc": {
        "name": "PHCC Patient Portal",
        "landing": "https://www.phcc.gov.qa/patient-portal",
        # Opening any portal page while signed-out redirects to the Cerner sign-in form, so this
        # is what we open FIRST to trigger the deterministic login (then we go to the real page).
        "login_url": "https://qphr.iqhealth.com/",
        "auth": "required",
        # After login the patient record lives at this templated URL.
        "record_url": "https://qphr.iqhealth.com/person/{patientID}/",
        "login_steps": [
            "Confirm with the user whether to LOG IN or REGISTER.",
            "Open https://www.phcc.gov.qa/patient-portal",
            'Click "Patient Portal Login".',
            "Collect username + password via the secure modal (request_credentials -> fill_login).",
            "If a captcha is shown, pause_for_user to solve it in the browser.",
            "After login, read the patientID from the URL (https://qphr.iqhealth.com/person/{patientID}/)."],
        "tasks": {
            "health_profile": {
                "name": "Health Profile",
                "intents": ["health profile", "allergies",
                            "immunisations", "immunizations", "health issues",
                            "الملف الصحي", "الحساسية", "التطعيمات", "المشاكل الصحية"],
                "requires_login": True,
                "read_only": True,
                "section_path": ["Health Record", "Health Profile"],
                "url": "https://qphr.iqhealth.com/person/{patientID}/",
                "inputs": [],
                "steps": [
                    'Click "Health Record" (collapsible sidebar item).',
                    'Click "Health Profile".',
                    "Extract: Current Medications, Immunisations, Current Allergies, Health Issues.",
                    'If the user asked to print, click "Print".',
                ],
            },
            "vitals_results": {
                "name": "Vitals and Latest Results",
                "intents": ["vitals", "lab results", "latest results", "test results",
                            "نتائج التحاليل", "نتائج المختبر", "العلامات الحيوية", "نتائج الفحوصات",
                            "تحاليلي", "نتائج المختبرية", "أحدث النتائج"],
                "requires_login": True,
                "read_only": True,
                "section_path": ["Health Record", "Vitals and Latest Results"],
                "url": "https://qphr.iqhealth.com/person/{patientID}/",
                "inputs": [],
                "steps": [
                    'Click "Health Record".',
                    'Click "Vitals and Latest Results".',
                    "Extract the patient-viewable results.",
                    'If the user asked to print, click "Print".',
                ],
            },
            "documents": {
                "name": "Medical Documents",
                "intents": ["medical documents", "download records", "medical reports",
                            "المستندات الطبية", "التقارير الطبية", "السجلات الطبية", "الوثائق الطبية"],
                "requires_login": True,
                "read_only": True,
                "section_path": ["Health Record", "Documents"],
                "url": "https://qphr.iqhealth.com/person/{patientID}/",
                "inputs": [],
                "steps": [
                    'Click "Health Record".',
                    'Click "Documents".',
                    "List the documents (each has a Download button).",
                    "Download per item only if the user asked.",
                    'If the user asked to print, click "Print".',
                ],
            },
            "medications": {
                "name": "Medications",
                "intents": ["my medications", "current medications", "what am i taking",
                            "what medications", "check my medications", "medications",
                            "أدويتي", "الأدوية", "قائمة الأدوية", "ما هي أدويتي", "دوائي", "علاجاتي"],
                "requires_login": True,
                "read_only": True,
                "section_path": ["Health Record", "Medications"],
                # Each medication hides Dose/Frequency/Route behind a "Show more info" toggle —
                # click them all to reveal the detail BEFORE reading the page.
                "expand_all": ["Show more info", "Show more information", "More info",
                               "More information", "Show more", "More details", "Details"],
                "answer_format": ("Present the medications as a Markdown table with columns: "
                                  "Medication | Dose | Frequency | Route. Add Date Started and "
                                  "Ordered By columns if those appear. One row per medication; "
                                  "leave a cell blank if that value isn't shown. If there are no "
                                  "medications, say so plainly instead of a table."),
                "url": "https://qphr.iqhealth.com/person/{patientID}/",
                "inputs": [],
                "steps": [
                    'Click "Health Record".',
                    'Click "Medications".',
                    'Click every "Show more info" link to reveal Dose / Frequency / Route.',
                    "Extract per item: name, Dose, Frequency, Route, Date Started On, Ordered By.",
                    'If the user asked to print, click "Print".',
                ],
            },
            "appointments": {
                "name": "Appointments",
                "intents": ["appointments", "upcoming appointment", "do i have an appointment",
                            "مواعيد", "موعد", "المواعيد", "مواعيدي", "موعدي", "حجوزاتي"],
                "requires_login": True,
                "read_only": True,
                "direct_read": True,           # the page shows the result directly → read+extract, no LLM loop
                "url": "https://qphr.iqhealth.com/appointments",
                "inputs": [],
                "steps": [
                    "Read the upcoming appointments.",
                    "If none, report 'no appointments scheduled'.",
                    'If the user asked to print, click "Print".',
                ],
            },
            "messaging_inbox": {
                "name": "Inbox",
                "intents": ["inbox", "received messages", "check messages",
                            "الرسائل الواردة", "البريد الوارد", "صندوق الوارد", "رسائلي الواردة"],
                "requires_login": True,
                "read_only": True,
                "direct_read": True,
                "url": "https://qphr.iqhealth.com/messaging/",
                "inputs": [],
                "steps": [
                    "Read the inbox messages.",
                    "If empty, report 'No messages received'."],
            },
            "messaging_outbox": {
                "name": "Sent Messages",
                "intents": ["sent messages", "outbox",
                            "الرسائل المرسلة", "البريد المرسل", "صندوق الصادر"],
                "requires_login": True,
                "read_only": True,
                "direct_read": True,
                "url": "https://qphr.iqhealth.com/messaging/outbox/",
                "inputs": [],
                "steps": [
                    "Read the sent messages.",
                    "If empty, report 'No messages sent'."],
            },
            "send_message": {
                "name": "Send a Message",
                "intents": ["send a message", "message my doctor", "contact clinic",
                            "email my doctor", "write to my clinic",
                            "إرسال رسالة", "أرسل رسالة", "راسل طبيبي", "مراسلة العيادة", "التواصل مع العيادة"],
                "requires_login": True,
                "read_only": False,            # WRITE -> dry-run gate before send
                "url": "https://qphr.iqhealth.com/messaging/",
                "inputs": [],
                "steps": [
                    'Click "Send a message".',
                    'Fill the "To" field.',
                    'Fill the "Subject" field.',
                    'Fill the "Message" field.',
                    "Attach files only if the user provided them.",
                    "DRY-RUN: show the full message and pause_for_user to review before sending.",
                    'Only after approval, click "Send".',
                ],
            },
        },
    },

    "moi": {
        "name": "MOI Qatar Portal",
        "auth": "none",                       # public inquiries, no login
        "tasks": {
            "traffic_violations": {
                "name": "Traffic Violations Inquiry",
                "intents": ["traffic violations", "traffic violation", "fines", "fine",
                            "mukhalafat", "violation inquiry", "violations", "traffic fines", "check traffic violations",
                            "المخالفات المرورية", "مخالفات المرور", "مخالفاتي", "المخالفات", "الغرامات", "مخالفات"],
                "requires_login": False,
                "read_only": True,
                "url": "https://fees2.moi.gov.qa/moipay/inquiry/violation",
                "pre_clicks": ["English"],     # switch the page to English first (if the link exists)
                # After the English switch + scroll back to the form, select the search-by mode:
                # the page defaults to another tab, so we click the "ID Number" tab before filling.
                "form_tabs": ["ID Number"],
                "inputs": ["qid"],
                "gates": ["captcha"],
                "steps": [
                    'Click the "ID Number" tab (navlink).',
                    'Fill the "Qatar ID" field with the QID.',
                    "CAPTCHA: pause_for_user to solve the verification code in the browser.",
                    'Click "Inquire" (confirm the exact label on the page).',
                    "Extract the violations / fines result."],
            },
            "pr_eligibility": {
                "name": "Permanent Residency Eligibility",
                "intents": ["permanent residency", "pr eligibility", "residency eligibility",
                            "permanent residence", "am i eligible for pr",
                            "الإقامة الدائمة", "أهلية الإقامة الدائمة", "أهلية الإقامة"],
                "requires_login": False,
                "read_only": True,
                "url": "https://portal.moi.gov.qa/wps/portal/MOIInternet/services/inquiries/residencypermits/preligibilityinquiry",
                "pre_clicks": ["English"],
                "inputs": ["qid", "residence_expiry_date"],
                "gates": ["captcha"],
                "steps": [
                    'Fill the "QID Number" field with the QID.',
                    'Fill the "Residence Expiry Date" — NOTE: this is THREE separate boxes '
                    "(year, month, day), not a single date picker. The smart date filler sets all three.",
                    "CAPTCHA: the human types the verification code.",
                    'Click "Submit".',
                    "Read the result popup (e.g. 'QID is not eligible to apply for Permanent Residency.')."],
            },
            "official_documents": {
                "name": "Official Documents Expiry",
                "intents": ["document expiry", "id expiry", "qid expiry", "passport expiry",
                            "residency expiry", "official documents", "when does my id expire",
                            "when does my passport expire", "document expiry dates",
                            "انتهاء جواز السفر", "تاريخ انتهاء الجواز", "انتهاء البطاقة", "انتهاء الوثائق",
                            "صلاحية الوثائق", "انتهاء الإقامة", "تاريخ انتهاء", "الوثائق الرسمية"],
                "requires_login": False,
                "read_only": True,
                "url": "https://portal.moi.gov.qa/wps/portal/MOIInternet/services/inquiries/others/officialdocuments",
                "pre_clicks": ["English"],
                "inputs": ["qid", "dob"],
                "gates": ["captcha"],
                "steps": [
                    'Fill the "QID Number" field with the QID.',
                    'Fill the "Date of Birth" field in yyyy/mm/dd (handle the date picker).',
                    "CAPTCHA: pause_for_user to solve the verification code.",
                    'Click "Submit" (confirm the exact label on the page).',
                    "Extract the table: columns [Document type, Date of Expiry]; rows "
                    "[ID Card Expiry Date, Passport Expiry Date, Residency Expiry Date]."],
            },
        },
    },

    # MOI E-Services authenticated portal (Tawtheeq login + OTP). NOT deep-linkable — flows run
    # AFTER sign-in, navigating by exact labels. Driven by the model loop following the playbook,
    # with the human gates the flow needs (credentials modal, OTP pause, dry-run before any write/
    # payment). login_flow + payment_flow are reused by every task.
    "moi_eservices": {
        "name": "MOI E-Services Portal",
        "landing": "https://eservices.moi.gov.qa/eservices-portal/pages/serviceGroups.xhtml",
        "auth": "required",
        # Deterministic navbar clicks to START the Tawtheeq sign-in (the model used to get stuck
        # on the landing page). After these, we request credentials; OTP + navigation follow.
        "login_clicks": ["English", "Login", "Tawtheeq"],
        # The Tawtheeq username/password fields live in this container (a <section>, not a <form>),
        # so the credential fill is scoped here.
        "login_form_selector": "#login-method",
        # This portal runs an (invisible) reCAPTCHA on sign-in; type the credentials with real
        # keystrokes + mouse movement so the session scores as a human, not a bot.
        "human_login": True,
        # After sign-in the portal asks for an OTP sent to the user's phone. We handle it
        # DETERMINISTICALLY: the user types the code in-app, we fill this field and click the
        # Continue button inside this form (id values confirmed from the live page).
        "otp_field_selector": "#otp-field",
        "otp_form_selector": "#mfaOtpFrm",
        # After the OTP, a sign-in modal opens (an option is preselected by default) with a Login
        # button in its footer. We click that button deterministically (ignoring the options), then
        # the model continues to the E-services Catalog.
        "post_login_modal": ["Login"],
        # Tawtheeq sign-in (rendered into the playbook; the human enters the OTP).
        "login_flow": [
            'Switch the site to English first (navbar "English" link).',
            'Click "Login" navigation bar link in the navbar.',
            'Choose the "Tawtheeq" login option (one of two options).',
            "Use request_credentials then fill_login (the user's username + password, entered securely).",
            'Click "Continue".',
            "OTP: a code is sent to the user's mobile — pause_for_user to enter it, then continue.",
            'If a login modal reopens with a default option preselected, click "Login".',
            'Scroll to the "E-services Catalog".',
        ],
        # Reusable NAPS payment sub-flow. The first "Pay" was already pressed (fill_service_dialog),
        # which opens the REVIEW PAYMENT page. review_payment is the ONLY payment tool the model
        # calls: once the user approves, the agent completes the whole checkout DETERMINISTICALLY
        # (click Pay → Payment Method dialog (saved card + Pay) → NAPS → Proceed to Payment → fill
        # card → confirm gate → Continue). This is in agent.py:_run_payment — the model used to skip
        # the review-page Pay, hallucinate tools, and click the gateway buttons too early.
        "payment_flow": [
            'You are now on the REVIEW PAYMENT confirmation page (it shows the Total Fees and the '
            'address details). Call review_payment — it shows the user the fee + details for their '
            'approval. This is the LAST tool you call for payment: once the user approves, I complete '
            'the ENTIRE checkout automatically (click Pay, pick the saved card in the Payment Method '
            'dialog and press Pay, click "NAPS" then "Proceed to Payment", fill the card, and pause '
            'for a final confirmation before pressing Continue). Do NOT click Pay, do NOT call '
            'confirm_payment_method / fill_payment_card, do NOT click "NAPS" or "Proceed to Payment", '
            'and NEVER open a gateway URL — just call review_payment and stop.'],
        "tasks": {
            "national_address_update": {
                "name": "Update National Address",
                "intents": ["update national address", "change my address", "edit national address",
                            "update my address",
                            "تحديث العنوان الوطني", "تغيير عنواني", "تعديل العنوان الوطني", "تحديث عنواني",
                            "حدّث عنواني", "حدث عنواني", "تحديث عنواني الوطني"],
                "requires_login": True,
                "read_only": False,            # WRITE → dry-run gate before the final Update
                "status": "verified",
                "navigation": [
                    'Click "General Services".',
                    'Click "National Address".',
                    'Click "National Address for Persons".',
                    'Dismiss the alert if it appears (click "OK").',
                    'Select the "Update National Address" radio option.',
                    'Click "Next".',
                ],
                "steps": [
                    'Right after the "Next" above, call edit_national_address (no arguments). It '
                    'clicks the page\'s "Update" button to make the address fields editable, '
                    'surfaces those fields to the user to review/change, then AUTOMATICALLY fills '
                    'their values, clicks "Next", confirms the acknowledgement/endorsement dialog '
                    '("Continue"), and reports whether it said "Transaction completed successfully". '
                    'This is the ONLY/LAST tool you call for this step — do NOT click "Update", fill '
                    'fields, click Next, or click Continue yourself.',
                ],
            },
            "national_address_certificate": {
                "name": "National Address Certificate",
                "intents": ["national address certificate", "address certificate",
                            "national address to email", "get national address certificate",
                            "شهادة العنوان الوطني", "شهادة عنواني الوطني", "شهادة العنوان",
                            "العنوان الوطني إلى بريدي", "إرسال شهادة العنوان"],
                "requires_login": True,
                "read_only": False,            # PAYMENT → dry-run gate
                "needs_payment": True,
                "status": "verified",
                "inputs": ["email"],           # certificate is emailed → ensure we have the address
                "navigation": [
                    'Click "General Services".',
                    'Click "National Address".',
                    'Click "National Address for Persons".',
                    'Dismiss the alert if it appears (click "OK").',
                    'Select the "National Address Certificate" radio option.',
                ],
                "steps": [
                    "After you select the certificate option, a service DIALOG (pop-up) opens with the "
                    "form. Call fill_service_dialog ONCE — it ticks the 'Home Address' checkbox, selects "
                    "English, fills the Email (injected from the user's saved profile), AND presses Pay. "
                    "Do NOT fill those fields, type the email, or click Pay yourself.",
                    "That first Pay opens the REVIEW PAYMENT page → now follow the PAYMENT sub-flow below "
                    "(review with the user, the card-type dialog, the bank gateway, the card details with a "
                    "confirm, then the OTP).",
                    "Report: the certificate has been sent to your email."],
            },
            "id_card_replacement": {
                "name": "Replace Lost / Damaged ID Card",
                "intents": ["replace lost id", "replace damaged id", "replace id card", "replace my id card",
                            "replace lost or damaged id", "lost qid", "damaged qid", "lost id card",
                            "damaged id card", "reissue id card", "reissue qid", "new id card",
                            "replace my qid", "lost my id", "damaged my id",
                            "استبدال البطاقة", "بدل فاقد", "بدل تالف", "استبدال بطاقة الهوية",
                            "استبدال البطاقة القطرية", "بطاقة بدل فاقد", "استبدال الهوية", "بطاقة تالفة",
                            "بطاقة مفقودة", "استبدال بطاقتي", "بطاقتي التالفة", "بطاقتي المفقودة",
                            "بطاقتي القطرية التالفة"],
                "requires_login": True,
                "read_only": False,            # PAYMENT → dry-run gate
                "needs_payment": True,
                "status": "verified",
                # The Expatriate Data form (radios + Next + OK + review) is filled DETERMINISTICALLY by
                # the agent the instant it appears (see agent._fill_id_card_and_review) — the model only
                # does the two navigation clicks below, then hands off.
                "deterministic_form": "id_card",
                "navigation": [
                    'Click "Residency".',
                    'Click "Replace Lost / Damaged ID Card".',
                ],
                "steps": [
                    'Click "Residency", then "Replace Lost / Damaged ID Card". The moment the '
                    '"Expatriate Data" form appears, I fill it AUTOMATICALLY (Service Type radio, "My '
                    'QID" radio, Next, the delivery "OK" dialog, and the payment review) — you do NOT '
                    'need to select the radios, click Next/OK, or call any form tool. Just do those two '
                    'navigation clicks and stop.'],
            },
            # ----- UNVERIFIED scaffolds: login + payment + gates are real & reused, but the
            # in-portal navigation labels are placeholders. The agent must READ the catalog and
            # CONFIRM the path with the user rather than trusting these labels. -----
            "qid_renewal": {
                "name": "QID / Residency Renewal",
                "intents": ["renew qid", "renew residency permit", "qid renewal", "residency renewal",
                            "تجديد البطاقة", "تجديد الإقامة", "تجديد البطاقة القطرية", "تجديد الرقم الشخصي"],
                "requires_login": True,
                "read_only": False,
                "needs_payment": True,
                "status": "unverified",
                "navigation": [
                    "(UNVERIFIED PATH) From the E-services Catalog, locate the QID / residency-renewal "
                    "service — read the page, then confirm the exact category → service → sub-service with the user."],
                "steps": [
                    "(UNVERIFIED) Complete the service form (eligibility / address / options).",
                    "Run the PAYMENT sub-flow if it is a paid service.",
                    'Verify "Transaction completed successfully".',
                ],
            },
            "driving_license_renewal": {
                "name": "Driving License Renewal",
                "intents": ["renew driving license", "license renewal", "renew driver license",
                            "renew driving licence",
                            "تجديد رخصة القيادة", "تجديد الرخصة", "تجديد رخصة السوق", "تجديد رخصة القياده"],
                "requires_login": True,
                "read_only": False,
                "needs_payment": True,
                "status": "unverified",
                "navigation": [
                    "(UNVERIFIED PATH) From the catalog, find Traffic Services → Renew Driving License — "
                    "read the page and confirm the path with the user."],
                "steps": [
                    "(UNVERIFIED) Complete eligibility / medical / address fields.",
                    "Run the PAYMENT sub-flow.",
                    'Verify "Transaction completed successfully".',
                ],
            },
            "pay_traffic_fines": {
                "name": "Pay Traffic Fines",
                "intents": ["pay traffic fines", "pay violations", "settle fines", "pay mukhalafat",
                            "pay my fines", "pay traffic violations",
                            "دفع المخالفات", "سداد المخالفات", "دفع الغرامات", "تسديد المخالفات",
                            "دفع مخالفاتي", "ادفع المخالفات", "سداد الغرامات"],
                "requires_login": True,
                "read_only": False,
                "needs_payment": True,
                "status": "unverified",
                "navigation": [
                    "(UNVERIFIED PATH) From the catalog, find the Traffic Violations / Payment service — "
                    "read the page and confirm the path with the user. (A public no-login inquiry exists at "
                    "fees2.moi.gov.qa; payment may branch from there — confirm which path applies.)"],
                "steps": [
                    "(UNVERIFIED) Select the violations to pay.",
                    "Run the PAYMENT sub-flow."],
            },
        },
    },
}


# --------------------------------------------------------------------------- #
# A WORKFLOW is one individual ACTION (e.g. "check traffic violations"), NOT a
# portal. Portals (MOI / PHCC) are just the place an action runs. We flatten the
# services above into a flat registry of action-workflows here.
# --------------------------------------------------------------------------- #
def _descriptor(svc_key: str, svc: dict[str, Any], task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"{svc_key}.{task_id}",     # stable workflow id
        "workflow_id": task_id,
        "service": svc_key,               # the portal this action runs on (metadata only)
        "service_name": svc.get("name", svc_key),
        "name": task.get("name", task_id),
        "intents": list(task.get("intents", [])),
        "auth": svc.get("auth", "none"),
        "requires_login": bool(task.get("requires_login")),
        "read_only": bool(task.get("read_only", True)),
        "direct_read": bool(task.get("direct_read", False)),
        "section_path": list(task.get("section_path", [])),
        "expand_all": list(task.get("expand_all", [])),     # links to click to reveal extra detail
        "answer_format": task.get("answer_format", ""),     # how to format the final answer (e.g. a table)
        "deterministic_form": task.get("deterministic_form", ""),  # agent auto-fills this form on sight
        "name_key": task_id,                                # stable task id (e.g. "id_card_replacement")
        "pre_clicks": list(task.get("pre_clicks", [])),
        "form_tabs": list(task.get("form_tabs", [])),   # in-page tabs to select before filling
        "navigation": list(task.get("navigation", [])),
        "needs_payment": bool(task.get("needs_payment", False)),
        "status": task.get("status", "verified"),
        "url": task.get("url", svc.get("landing", "")),
        "inputs": list(task.get("inputs", [])),
        "gates": list(task.get("gates", [])),
        "steps": list(task.get("steps", [])),
        "login_steps": list(svc.get("login_steps", [])),
        "login_flow": list(svc.get("login_flow", [])),
        "login_clicks": list(svc.get("login_clicks", [])),
        "login_form_selector": svc.get("login_form_selector", ""),
        "human_login": bool(svc.get("human_login", False)),   # type credentials human-like (anti-reCAPTCHA)
        "otp_field_selector": svc.get("otp_field_selector", ""),   # deterministic OTP step (in-app code → fill + submit)
        "otp_form_selector": svc.get("otp_form_selector", ""),
        "post_login_modal": list(svc.get("post_login_modal", [])), # modal Login button to click after OTP
        "payment_flow": list(svc.get("payment_flow", [])),
        "login_url": svc.get("login_url", ""),
        "record_url": svc.get("record_url", ""),
        "landing": svc.get("landing", ""),
    }


# Flat registry: every individual action is its own workflow.
WORKFLOWS: list[dict[str, Any]] = [
    _descriptor(svc_key, svc, task_id, task)
    for svc_key, svc in SERVICES.items()
    for task_id, task in svc.get("tasks", {}).items()
]


def list_workflows() -> list[dict[str, Any]]:
    """All known action-workflows (id, name, portal, inputs) — for transparency/UI."""
    return [{"id": w["id"], "name": w["name"], "portal": w["service_name"],
             "inputs": w["inputs"], "read_only": w["read_only"]} for w in WORKFLOWS]


# --------------------------------------------------------------------------- #
# Intent matching
# --------------------------------------------------------------------------- #
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


# Pay verbs (EN + AR) — boost the PAYMENT workflow over a read-only inquiry that shares a noun
# (e.g. "pay my traffic fines" / "دفع مخالفاتي" → the payment task, not the violations inquiry).
_PAY_VERBS = ("pay", "settle", "دفع", "ادفع", "سداد", "تسديد", "تسديد")


def match_workflow(message: str) -> dict[str, Any] | None:
    """Return the best-matching action-workflow for a user message, or None."""
    text = _norm(message)
    if not text:
        return None

    best: tuple[int, dict[str, Any]] | None = None
    for wf in WORKFLOWS:
        score = 0
        for phrase in wf.get("intents", []):
            p = _norm(phrase)
            if not p:
                continue
            if p in text:
                # Longer, more specific phrases win over generic single words.
                score = max(score, 10 + len(p))
            else:
                # All words of a short multi-word intent present (any order).
                words = p.split()
                if len(words) > 1 and all(w in text for w in words):
                    score = max(score, 5 + len(p))
        # "Pay/settle …" should route to the PAYMENT workflow, not a read-only inquiry that
        # happens to share a noun (e.g. "pay my traffic fines" vs the fines-inquiry page).
        if score and wf.get("needs_payment") and any(v in text for v in _PAY_VERBS):
            score += 20
        if score and (best is None or score > best[0]):
            best = (score, wf)
    return best[1] if best else None


# Backwards-compatible alias (the matched action IS the workflow).
match_task = match_workflow


def required_inputs(task: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve a task's input keys into full specs (label, profile field, format…)."""
    out: list[dict[str, Any]] = []
    for key in task.get("inputs", []):
        spec = dict(INPUT_SPECS.get(key, {"profile": key, "label": key, "type": "text"}))
        spec["key"] = key
        out.append(spec)
    return out


def resolve_url(task: dict[str, Any], values: dict[str, str] | None = None) -> str:
    """Fill {placeholders} in the deep-link URL from captured/profile values."""
    url = task.get("url", "")
    for k, v in (values or {}).items():
        url = url.replace("{" + k + "}", str(v))
    return url


def build_playbook(task: dict[str, Any], values: dict[str, str] | None = None) -> str:
    """Build the strict, authoritative instruction block injected into the planner.

    It states the exact deep link, the resolved input values to type, the ordered
    labelled steps, and the human gates — so the model follows rather than invents.
    """
    values = values or {}
    lines: list[str] = []
    lines.append(f"AUTHORITATIVE WORKFLOW — {task['service_name']} · {task['name']}.")
    lines.append("Follow these steps EXACTLY and in order. Do NOT invent URLs, labels, or extra steps.")
    lines.append(f"The correct page is ALREADY OPEN: {resolve_url(task, values)}")

    if task.get("status") == "unverified":
        lines.append("⚠ The in-portal navigation for this service is NOT yet verified. Do NOT trust the "
                     "placeholder labels — READ the page, find the right service in the catalog, and CONFIRM "
                     "the path with the user (pause_for_user) before acting.")

    # Multi-step (Tawtheeq) sign-in flow, when present.
    if task.get("login_flow"):
        lines.append("CRITICAL: navigate ONLY by CLICKING links/buttons/tabs. NEVER call open_page on a "
                     "portal/gateway URL — this portal errors ('already logged in elsewhere') and drops your "
                     "session. If a click seems not to work, re-read the page (see_page) and click again; do "
                     "not 'recover' by opening a URL.")
        lines.append("SIGN-IN (do this first, in order):")
        for i, step in enumerate(task["login_flow"], 1):
            lines.append(f"  L{i}. {step}")
    elif task.get("requires_login"):
        lines.append("This service needs sign-in. First confirm with the user (login or register), "
                     "then use request_credentials -> fill_login. Solve any captcha via pause_for_user.")

    # In-portal navigation to reach the service form.
    if task.get("navigation"):
        lines.append("NAVIGATE to the service (after sign-in):")
        for i, step in enumerate(task["navigation"], 1):
            lines.append(f"  N{i}. {step}")

    # Known input values to type (resolved from the user's saved info / what they just gave).
    specs = required_inputs(task)
    if specs:
        lines.append("Values to enter (use see_page then fill_mark by number into the matching field):")
        for s in specs:
            val = values.get(s["key"], "")
            shown = val if val else "(ASK the user — not provided)"
            fmt = f" [format {s['format']}]" if s.get("format") else ""
            lines.append(f"  • {s['label']}{fmt}: {shown}")
        if any(s.get("type") == "date" for s in specs):
            lines.append("For any DATE field, prefer the fill_date tool — it handles date-picker widgets.")

    lines.append("Steps:")
    for i, step in enumerate(task.get("steps", []), 1):
        lines.append(f"  {i}. {step}")

    # Reusable NAPS payment sub-flow (only for paid services).
    if task.get("needs_payment") and task.get("payment_flow"):
        lines.append("PAYMENT sub-flow (uses the user's saved payment card; review before paying):")
        for i, step in enumerate(task["payment_flow"], 1):
            lines.append(f"  P{i}. {step}")

    if task.get("gates"):
        lines.append(f"Human gates (pause_for_user, human solves in the visible browser): {', '.join(task['gates'])}.")

    if not task.get("read_only", True):
        lines.append("This task WRITES data (or pays). Before the final submit/send/pay, ALWAYS pause_for_user "
                     "with a full review of what will be sent (dry-run). Never submit or pay without approval.")

    lines.append("When you have the requested data on screen, finalize with a clear answer in the user's language.")
    return "\n".join(lines)
