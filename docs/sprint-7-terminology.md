# Relay Sprint 7 English–Arabic terminology

This map is the operator vocabulary used throughout the application and Help. Product nouns stay consistent across navigation, headings, actions, status labels, and guidance. Machine identifiers remain in their source form and are isolated from surrounding RTL text.

| English | Arabic | Usage note |
|---|---|---|
| Dashboard | لوحة التحكم | Operational overview |
| Transactions | المعاملات | Receipt-derived transaction records |
| Transaction details | تفاصيل المعاملة | One transaction and its evidence |
| Duplicates | المعاملات المكررة | Deterministic duplicate groups |
| Duplicate group | مجموعة معاملات مكررة | Canonical record plus evidence members |
| Canonical | السجل الأساسي | Primary record selected by existing rules |
| Review | المراجعة | Human decision workflow |
| Processing | المعالجة | Receipt processing queue |
| Reports | التقارير | Reporting and Excel export |
| Devices | الأجهزة | Enrolled Android sources |
| Configuration | الإعدادات | Operator-visible system configuration |
| Help | المساعدة | Bilingual operator guidance |
| Collected | تم الجمع | Evidence received from the approved source |
| Uploaded | تم الرفع | Evidence accepted by Relay |
| Processed | تمت المعالجة | Processing attempt completed |
| OCR extracted | تم استخراج النص | OCR produced machine-readable text |
| Classified | تم التصنيف | Existing deterministic classification completed |
| Reviewed | تمت المراجعة | Human review recorded |
| Final | نهائي | Existing final state; not transaction proof |
| Needs review | يحتاج إلى مراجعة | Human attention is required |
| Pending | قيد الانتظار | Waiting for work |
| Processing (status) | جارٍ المعالجة | Work is in progress |
| Succeeded | نجحت | Work completed successfully |
| Failed | فشلت | Work did not complete |
| Skipped | تم التخطي | Work was intentionally not run |
| Confident | موثوق | Strong extracted-field confidence |
| Uncertain | غير مؤكد | Operator should inspect the field |
| Missing | مفقود | Expected field was not extracted |
| Invalid | غير صالح | Value failed validation |
| Reference / serial | الرقم المرجعي / التسلسلي | Render value with LTR bidi isolation |
| Sender | المرسل | Preserve user/provider name as supplied |
| Recipient | المستلم | Preserve user/provider name as supplied |
| Amount | المبلغ | Keep digits and currency in logical order |
| Currency | العملة | Keep source currency code recognizable |
| Source | المصدر | Collection channel or origin |
| Receipt | الإيصال | Collected evidence; not proof of payment |
| Receipt image | صورة الإيصال | Protected original evidence |
| Retry | إعادة المحاولة | Available only when backend allows it |
| Export to Excel | تصدير إلى Excel | Product name remains recognizable |

Arabic wording was written locally without an external translation API. The pilot must still record feedback from representative Arabic-speaking operators; no claim of linguistic certification is made before that review.
