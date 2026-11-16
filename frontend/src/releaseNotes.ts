/** What changed in each portal version, newest first. Add an entry (and bump
 * package.json "version") with every deploy that users should hear about. */
export type ReleaseNote = { version: string; date: string; en: string[]; ar: string[] };

export const releaseNotes: ReleaseNote[] = [
  {
    version: "0.8.0",
    date: "2026-11-16",
    en: [
      "Receipts are confirmed when an incoming bank SMS with the same amount arrives the same day.",
      "Low-quality WhatsApp receipts are now read; payment requests are rejected and labelled.",
      "Photos that are not receipts are hidden from Transactions, Review and the dashboard.",
      "Transactions and Review use the same dates as the dashboard (when the image reached the phone), with time filters and newest first.",
      "Suggested reference, amount, sender and recipient are shown until confirmed.",
      "New setting: who receipts must be sent to (Email settings page).",
      "This 'What's new' window after each update.",
    ],
    ar: [
      "الإيصال بيتأكد لما توصل رسالة بنك واردة بنفس المبلغ في نفس اليوم.",
      "إيصالات الواتساب الضعيفة بقت بتتقري، وطلبات الدفع بتترفض وعليها علامة.",
      "الصور اللي مش إيصالات بتستخبى من المعاملات والمراجعة والداشبورد.",
      "المعاملات والمراجعة بقت بنفس تواريخ الداشبورد (وقت وصول الصورة للموبايل)، وفيها فلتر الوقت والأحدث الأول.",
      "المرجع والمبلغ والمرسل والمستلم المقترحين بيظهروا لحد ما يتأكدوا.",
      "إعداد جديد: الإيصالات لازم تتبعت لمين (صفحة إعدادات الإيميل).",
      "شاشة «الجديد» دي بعد كل تحديث.",
    ],
  },
];

const SEEN_KEY = "relay.whatsNewSeen";

export function seenVersion(): string | null {
  try { return window.localStorage.getItem(SEEN_KEY); } catch { return null; }
}

export function markSeen(version: string): void {
  try { window.localStorage.setItem(SEEN_KEY, version); } catch { /* storage off: show again next time */ }
}
