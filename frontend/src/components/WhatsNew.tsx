import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import { useI18n } from "../i18n";
import { markSeen, releaseNotes, seenVersion, type ReleaseNote } from "../releaseNotes";

/** Shown once after each portal update: what changed in the newest version. */
export function WhatsNew({ notes = releaseNotes, enabled = import.meta.env.MODE !== "test" }: { notes?: ReleaseNote[]; enabled?: boolean }) {
  const { locale, messages: m } = useI18n(); const latest = notes[0];
  const [open, setOpen] = useState(() => enabled && !!latest && seenVersion() !== latest.version);
  if (!latest) return null;
  const close = () => { markSeen(latest.version); setOpen(false); };
  return <Dialog.Root open={open} onOpenChange={(value) => { if (!value) close(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="dialog-overlay" />
      <Dialog.Content className="dialog whats-new" aria-describedby={undefined}>
        <Dialog.Title>{m.whatsNew.title.replace("{version}", latest.version)}</Dialog.Title>
        <ul className="whats-new-list">{(locale === "ar" ? latest.ar : latest.en).map((item) => <li key={item}>{item}</li>)}</ul>
        <div className="dialog-actions"><button type="button" onClick={close}>{m.whatsNew.ok}</button></div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
