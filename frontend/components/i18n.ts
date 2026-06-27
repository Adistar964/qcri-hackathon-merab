// Lightweight, dependency-free i18n for the FANAR platform chrome.
//
// Scope on purpose: this localises the PLATFORM UI (nav, buttons, the human-in-the-loop panel,
// hero, helper text). It does NOT translate:
//   • the website the agent drives (its buttons like "Continue" are pressed in English),
//   • the user's DATA values (My Info, saved credentials, saved card values stay English),
//   • the agent's steps / questions / answers — those are localised by the BACKEND so Fanar
//     speaks Arabic natively (see agent.py _localize / _lang_instr).

export type Lang = "en" | "ar";

type Dict = Record<string, { en: string; ar: string }>;

const STRINGS: Dict = {
  // ── Nav / chrome ────────────────────────────────────────────────────────
  "badge.web": { en: "Web", ar: "ويب" },
  "badge.desktop": { en: "Desktop", ar: "سطح المكتب" },
  "nav.history": { en: "History", ar: "السجل" },
  "nav.historyTitle": { en: "History — past conversations & audit trails", ar: "السجل — المحادثات السابقة وسجلّات التتبّع" },
  "nav.myInfo": { en: "My Info", ar: "معلوماتي" },
  "nav.myInfoTitle": { en: "My Information — saved info, payment card & logins (auto-fill)", ar: "معلوماتي — البيانات المحفوظة وبطاقة الدفع وبيانات الدخول (تعبئة تلقائية)" },
  "nav.new": { en: "New", ar: "جديد" },
  "nav.newTitle": { en: "Start a new conversation", ar: "بدء محادثة جديدة" },
  "nav.deleteTitle": { en: "Delete this conversation", ar: "حذف هذه المحادثة" },
  "nav.voice": { en: "Voice", ar: "الصوت" },
  "nav.on": { en: "On", ar: "تشغيل" },
  "nav.off": { en: "Off", ar: "إيقاف" },
  "nav.voiceTitle": { en: "Speak replies (Fanar TTS)", ar: "نطق الردود (تحويل النص إلى كلام من فنار)" },
  "nav.langTitle": { en: "Switch language", ar: "تبديل اللغة" },
  "nav.minimize": { en: "Minimize", ar: "تصغير" },
  "nav.close": { en: "Close", ar: "إغلاق" },

  "mode.chat": { en: "Chat", ar: "محادثة" },
  "mode.agent": { en: "Agent", ar: "الوكيل" },

  // ── Command bar ─────────────────────────────────────────────────────────
  "cmd.placeholderAgent": { en: "Command the agent…", ar: "أصدر أمرًا للوكيل…" },
  "cmd.placeholderChat": { en: "Message Fanar…", ar: "راسل فنار…" },
  "cmd.send": { en: "Send", ar: "إرسال" },
  "cmd.stop": { en: "Stop", ar: "إيقاف" },
  "cmd.helperAgentDesktop": { en: "Agent can see your screen, control your computer & browser — it pauses for your approval", ar: "يستطيع الوكيل رؤية شاشتك والتحكّم في حاسوبك ومتصفّحك — ويتوقّف لانتظار موافقتك" },
  "cmd.helperAgentWeb": { en: "Agent drives a real browser & pauses for you to log in", ar: "يشغّل الوكيل متصفّحًا حقيقيًا ويتوقّف لتسجيل دخولك" },
  "cmd.helperChat": { en: "Direct chat with Fanar", ar: "محادثة مباشرة مع فنار" },

  // ── Agent-locked banner ─────────────────────────────────────────────────
  "locked.note": { en: "Agent runs one task per conversation. Start a new one to run another.", ar: "ينفّذ الوكيل مهمة واحدة لكل محادثة. ابدأ محادثة جديدة لتنفيذ مهمة أخرى." },
  "locked.new": { en: "New Conversation", ar: "محادثة جديدة" },

  // ── Mode-switch modal ───────────────────────────────────────────────────
  "switch.title": { en: "Switch to {mode} mode?", ar: "التبديل إلى وضع {mode}؟" },
  "switch.bodyBusy": { en: "The agent is still working on this task. Switching stops it and starts a new conversation.", ar: "لا يزال الوكيل يعمل على هذه المهمة. التبديل سيوقفها ويبدأ محادثة جديدة." },
  "switch.body": { en: "This starts a new conversation — the current one will be cleared.", ar: "سيؤدي هذا إلى بدء محادثة جديدة — وستُمسح المحادثة الحالية." },
  "switch.confirm": { en: "Switch & Start New", ar: "تبديل وبدء جديد" },
  "switch.cancel": { en: "Cancel", ar: "إلغاء" },

  // ── Hero ────────────────────────────────────────────────────────────────
  "hero.title1": { en: "Agentic AI", ar: "ذكاء اصطناعي وكيل" },
  "hero.title2": { en: "for Qatar", ar: "لخدمة قطر" },
  "hero.subtitle": { en: "One agent that acts — it drives a real browser, fills forms, logs in, writes documents, and speaks. Built on Fanar across government, healthcare & education.", ar: "وكيلٌ واحد ينفّذ المهام — يشغّل متصفّحًا حقيقيًا، ويعبّئ النماذج، ويسجّل الدخول، ويكتب المستندات، ويتحدّث. مبنيٌّ على فنار في مجالات الحكومة والصحة والتعليم." },
  "hero.tryAgent": { en: "Try an agent task", ar: "جرّب مهمة للوكيل" },
  "hero.tryQuestion": { en: "Try a question", ar: "جرّب سؤالاً" },

  // ── Live preview ────────────────────────────────────────────────────────
  "live.browser": { en: "Live Browser", ar: "المتصفّح المباشر" },
  "live.screen": { en: "Live Screen", ar: "الشاشة المباشرة" },
  "live.standby": { en: "standby", ar: "استعداد" },
  "live.maximize": { en: "Maximize", ar: "تكبير" },
  "live.close": { en: "Close", ar: "إغلاق" },
  "live.viewDesktop": { en: "The agent's view of your screen appears here", ar: "تظهر هنا رؤية الوكيل لشاشتك" },
  "live.viewWeb": { en: "The agent's live browser appears here", ar: "يظهر هنا متصفّح الوكيل المباشر" },
  "status.waiting": { en: "waiting", ar: "بانتظار" },
  "status.live": { en: "live", ar: "مباشر" },
  "status.idle": { en: "idle", ar: "خامل" },

  // ── Credentials gate ────────────────────────────────────────────────────
  "creds.title": { en: "Enter Credentials", ar: "أدخل بيانات الدخول" },
  "creds.masked": { en: "Masked & sent only to your local backend to fill the form — never shown to the AI.", ar: "تُخفى وتُرسل فقط إلى الخادم المحلي لديك لتعبئة النموذج — ولا تُعرض أبدًا على الذكاء الاصطناعي." },
  "creds.rememberLogin": { en: "Remember this login for this site (saved locally, in My Info → Credentials)", ar: "تذكّر بيانات الدخول لهذا الموقع (تُحفظ محليًا في معلوماتي ← بيانات الدخول)" },
  "creds.login": { en: "Log In For Me", ar: "سجّل الدخول نيابةً عني" },

  // ── Info / edit gate ────────────────────────────────────────────────────
  "info.title": { en: "A Few Details", ar: "بعض التفاصيل" },
  "edit.title": { en: "Review & Save", ar: "المراجعة والحفظ" },
  "info.notFound": { en: "Not found in your saved info. Enter it once and I'll continue.", ar: "غير موجود في معلوماتك المحفوظة. أدخله مرة واحدة وسأكمل." },
  "info.saveNext": { en: "Save to my info for next time", ar: "احفظ في معلوماتي للمرّة القادمة" },
  "btn.continue": { en: "Continue →", ar: "متابعة →" },
  "edit.save": { en: "Save Update →", ar: "حفظ التحديث →" },

  // ── Captcha / OTP gate ──────────────────────────────────────────────────
  "captcha.title": { en: "Verification Code", ar: "رمز التحقّق" },
  "captcha.placeholder": { en: "Type the code shown above", ar: "اكتب الرمز الظاهر أعلاه" },
  "captcha.fillSubmit": { en: "Fill & Submit →", ar: "تعبئة وإرسال →" },
  "captcha.imDone": { en: "I'm Done — Continue", ar: "انتهيت — متابعة" },
  "otp.title": { en: "One-Time Code (OTP)", ar: "رمز التحقّق لمرّة واحدة (OTP)" },
  "otp.placeholder": { en: "Enter the code sent to your phone", ar: "أدخل الرمز المُرسل إلى هاتفك" },
  "otp.fillContinue": { en: "Fill & Continue →", ar: "تعبئة ومتابعة →" },

  // ── Review gate ─────────────────────────────────────────────────────────
  "review.title": { en: "Review Needed", ar: "مطلوب مراجعة" },
  "review.totalFee": { en: "Total Fee Amount", ar: "إجمالي الرسوم" },
  "review.details": { en: "Details", ar: "التفاصيل" },
  "review.email": { en: "This will be sent to your email:", ar: "سيُرسل هذا إلى بريدك الإلكتروني:" },
  "review.approve": { en: "Approve & Continue →", ar: "الموافقة والمتابعة →" },

  // ── Generic confirm / login gate ────────────────────────────────────────
  "gen.approveAction": { en: "Approve Action", ar: "الموافقة على الإجراء" },
  "gen.confirmToContinue": { en: "Confirm to Continue", ar: "أكّد للمتابعة" },
  "gen.loginNeeded": { en: "Login Needed", ar: "مطلوب تسجيل الدخول" },
  "gen.confirmDesktopHint": { en: "The agent wants to act on your computer. Approve to let it proceed.", ar: "يريد الوكيل تنفيذ إجراء على حاسوبك. وافق للسماح له بالمتابعة." },
  "gen.confirmWebHint": { en: "Review the details above, then approve to let the agent continue.", ar: "راجع التفاصيل أعلاه، ثم وافق للسماح للوكيل بالمتابعة." },
  "gen.completeStepHint": { en: "Complete the step (OTP / captcha / submit) in the window, then continue.", ar: "أكمل الخطوة (رمز التحقق / الكابتشا / الإرسال) في النافذة، ثم تابع." },
  "gen.optionalNote": { en: "Optional note…", ar: "ملاحظة اختيارية…" },
  "gen.approveRun": { en: "Approve & Run", ar: "الموافقة والتشغيل" },
  "gen.imDone": { en: "I'm Done — Continue", ar: "انتهيت — متابعة" },
  "gen.skip": { en: "Skip", ar: "تخطّي" },
  "btn.cancel": { en: "Cancel", ar: "إلغاء" },

  // ── Artefacts ───────────────────────────────────────────────────────────
  "artefacts.title": { en: "Artefacts", ar: "الملفات الناتجة" },

  // ── My Information panel (chrome only — the user's DATA values stay English) ──
  "panel.title": { en: "My Information", ar: "معلوماتي" },
  "panel.tabInfo": { en: "My Info", ar: "معلوماتي" },
  "panel.tabPayment": { en: "Payment", ar: "الدفع" },
  "panel.tabCredentials": { en: "Credentials", ar: "بيانات الدخول" },
  "panel.close": { en: "Close", ar: "إغلاق" },
  "panel.done": { en: "Done", ar: "تم" },
  "panel.save": { en: "Save", ar: "حفظ" },
  "panel.saving": { en: "Saving…", ar: "جارٍ الحفظ…" },
  "panel.saved": { en: "Saved", ar: "تم الحفظ" },
  "panel.saveCard": { en: "Save Card", ar: "حفظ البطاقة" },
  "panel.uploadQid": { en: "Upload QID", ar: "رفع البطاقة القطرية" },
  "panel.uploadQidHelp": { en: "Upload a photo of your Qatar ID and Fanar will read it and fill the fields below automatically.", ar: "ارفع صورة لبطاقتك القطرية وسيقرؤها فنار ويعبّئ الحقول أدناه تلقائيًا." },
  "panel.readingQid": { en: "Reading QID…", ar: "جارٍ قراءة البطاقة…" },
  "panel.chooseQid": { en: "Choose QID image", ar: "اختر صورة البطاقة" },
  "panel.detectedFilled": { en: "Detected & filled:", ar: "تم الكشف والتعبئة:" },
  "panel.noFieldsRead": { en: "No fields could be read — try a clearer photo or enter them below.", ar: "تعذّرت قراءة أي حقول — جرّب صورة أوضح أو أدخلها أدناه." },
  "panel.paymentHelp": { en: "Optional. When a task needs a payment (e.g. paying a fine), the agent uses this card to fill the checkout form. Stored locally only — never sent to the AI.", ar: "اختياري. عندما تتطلّب مهمة دفعًا (مثل سداد مخالفة)، يستخدم الوكيل هذه البطاقة لتعبئة نموذج الدفع. تُحفظ محليًا فقط — ولا تُرسل أبدًا إلى الذكاء الاصطناعي." },
  "panel.showCardCvv": { en: "Show card number & CVV", ar: "إظهار رقم البطاقة ورمز CVV" },
  "panel.removeCard": { en: "Remove saved card", ar: "إزالة البطاقة المحفوظة" },
  "panel.credHelp": { en: "Saved logins (like a password manager). When the agent needs to sign in to a saved site, it uses these automatically. Stored locally only — never sent to the AI.", ar: "بيانات دخول محفوظة (مثل مدير كلمات المرور). عندما يحتاج الوكيل لتسجيل الدخول إلى موقع محفوظ، يستخدمها تلقائيًا. تُحفظ محليًا فقط — ولا تُرسل أبدًا إلى الذكاء الاصطناعي." },
  "panel.credEmpty": { en: "No saved logins yet. When the agent logs you in, tick “Remember this login”, or add one below.", ar: "لا توجد بيانات دخول محفوظة بعد. عندما يسجّل الوكيل دخولك، ضع علامة على “تذكّر بيانات الدخول”، أو أضف واحدة أدناه." },
  "panel.username": { en: "username", ar: "اسم المستخدم" },
  "panel.password": { en: "password", ar: "كلمة المرور" },
  "panel.show": { en: "Show", ar: "إظهار" },
  "panel.hide": { en: "Hide", ar: "إخفاء" },
  "panel.delete": { en: "Delete", ar: "حذف" },
  "panel.addLogin": { en: "Add a login", ar: "إضافة بيانات دخول" },
  "panel.addLoginBtn": { en: "Add login", ar: "إضافة" },
  "panel.labelOptional": { en: "label (optional)", ar: "تسمية (اختياري)" },

  // ── History panel ──────────────────────────────────────────────────────
  "hist.title": { en: "History", ar: "السجل" },
  "hist.subtitle": { en: "Past conversations & audit trails", ar: "المحادثات السابقة وسجلّات التتبّع" },
  "hist.empty": { en: "No conversations yet.", ar: "لا توجد محادثات بعد." },
  "hist.back": { en: "Back", ar: "رجوع" },
  "hist.delete": { en: "Delete", ar: "حذف" },
  "hist.steps": { en: "steps", ar: "خطوات" },
  "hist.actions": { en: "actions", ar: "إجراءات" },
  "hist.agent": { en: "Agent", ar: "الوكيل" },
  "hist.chat": { en: "Chat", ar: "محادثة" },
  "hist.loading": { en: "Loading…", ar: "جارٍ التحميل…" },
  "hist.noConvs": { en: "No past conversations yet.", ar: "لا توجد محادثات سابقة بعد." },
  "hist.conversation": { en: "Conversation", ar: "محادثة" },
  "hist.finalAnswer": { en: "Final Answer", ar: "الإجابة النهائية" },
  "hist.close": { en: "Close", ar: "إغلاق" },
};

// Human labels for saved-profile / payment fields (values stay English; only the LABEL localises).
const FIELD_LABELS: Dict = {
  full_name: { en: "Full Name", ar: "الاسم الكامل" },
  email: { en: "Email", ar: "البريد الإلكتروني" },
  phone: { en: "Phone", ar: "الهاتف" },
  dob: { en: "Date of Birth", ar: "تاريخ الميلاد" },
  qid: { en: "Qatar ID (QID)", ar: "البطاقة القطرية (الرقم الشخصي)" },
  qid_expiry: { en: "QID Expiry", ar: "انتهاء البطاقة القطرية" },
  passport: { en: "Passport Number", ar: "رقم جواز السفر" },
  passport_expiry: { en: "Passport Expiry", ar: "انتهاء جواز السفر" },
  residence_expiry: { en: "Residence Permit Expiry", ar: "انتهاء تصريح الإقامة" },
  nationality: { en: "Nationality", ar: "الجنسية" },
  card_type: { en: "Card Type", ar: "نوع البطاقة" },
  cardholder_name: { en: "Cardholder Name", ar: "اسم حامل البطاقة" },
  card_number: { en: "Card Number", ar: "رقم البطاقة" },
  expiry: { en: "Expiry (MM/YY)", ar: "تاريخ الانتهاء (شهر/سنة)" },
  cvv: { en: "CVV", ar: "رمز التحقق CVV" },
  billing_zip: { en: "Billing Postal Code", ar: "الرمز البريدي للفوترة" },
};

/** Translate a UI key into `lang`. Supports {var} interpolation. Falls back to English, then the key. */
export function tr(lang: Lang, key: string, vars?: Record<string, string>): string {
  const entry = STRINGS[key];
  let s = entry ? (entry[lang] ?? entry.en) : key;
  if (vars) for (const k of Object.keys(vars)) s = s.replace(new RegExp(`\\{${k}\\}`, "g"), vars[k]);
  return s;
}

/** Localise a saved-profile / payment field LABEL (the value itself stays English). Falls back to
 *  the backend-provided English label for unknown keys. */
export function fieldLabel(lang: Lang, key: string, fallback: string): string {
  const entry = FIELD_LABELS[key];
  return entry ? (entry[lang] ?? entry.en) : fallback;
}

/** Bind a translator to a language: `const t = makeT(lang); t("nav.new")`. */
export function makeT(lang: Lang) {
  return (key: string, vars?: Record<string, string>) => tr(lang, key, vars);
}
