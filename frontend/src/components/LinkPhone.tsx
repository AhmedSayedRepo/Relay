import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, send } from "../api";
import { formatMessage, useI18n } from "../i18n";
import qrcodegen from "../vendor/qrcodegen";
import { Icon } from "./Brand";

type Challenge = { challenge: string; expires_at: string };

/** Renders the code as crisp SVG modules; dark on light regardless of theme so phones scan it. */
export function QrCode({ value, label }: { value: string; label: string }) {
  const path = useMemo(() => {
    const qr = qrcodegen.QrCode.encodeText(value, qrcodegen.QrCode.Ecc.MEDIUM);
    const parts: string[] = [];
    for (let y = 0; y < qr.size; y++) for (let x = 0; x < qr.size; x++) if (qr.getModule(x, y)) parts.push(`M${x + 4},${y + 4}h1v1h-1z`);
    return { d: parts.join(""), size: qr.size + 8 };
  }, [value]);
  return <svg className="qr-code" viewBox={`0 0 ${path.size} ${path.size}`} role="img" aria-label={label} shapeRendering="crispEdges">
    <rect width={path.size} height={path.size} fill="#fff" /><path d={path.d} fill="#0d1b1e" />
  </svg>;
}

function useCountdown(expiresAt: string | undefined) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!expiresAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [expiresAt]);
  const left = expiresAt ? Math.max(0, Math.round((Date.parse(expiresAt) - now) / 1000)) : 0;
  return { left, text: `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` };
}

function LinkBody({ deviceId, token }: { deviceId: string; token: string }) {
  const { messages: m } = useI18n();
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [needsReplace, setNeedsReplace] = useState(false); const [replace, setReplace] = useState(false);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false); const [copied, setCopied] = useState(false);
  const { left, text } = useCountdown(challenge?.expires_at);
  const create = useCallback(async (replaceExisting: boolean) => {
    setBusy(true); setError(""); setCopied(false);
    try {
      setChallenge(await send<Challenge>(`/devices/${deviceId}/enrollment-challenges`, { replace_existing: replaceExisting }, { token }));
      setNeedsReplace(false);
    } catch (reason) {
      setChallenge(null);
      // 409: a phone is already linked; replacing it needs an explicit choice.
      if (reason instanceof ApiError && reason.status === 409 && !replaceExisting) setNeedsReplace(true);
      else setError(m.link.failed);
    } finally { setBusy(false); }
  }, [deviceId, token, m.link.failed]);
  useEffect(() => { void create(false); }, [create]);
  function copy(value: string) { void navigator.clipboard?.writeText(value).then(() => setCopied(true)).catch(() => undefined); }

  if (busy && !challenge) return <p className="status loading" role="status"><span aria-hidden="true" />{m.link.working}</p>;
  if (needsReplace) return <div className="replace-step">
    <p>{m.link.replaceHint}</p>
    <label className="toggle-row"><input type="checkbox" checked={replace} onChange={(event) => setReplace(event.target.checked)} />{m.link.replace}</label>
    <button type="button" disabled={!replace || busy} onClick={() => void create(true)}>{m.link.newCode}</button>
  </div>;
  if (!challenge) return <><p className="status error" role="alert">{error || m.link.failed}</p><button type="button" onClick={() => void create(false)}>{m.link.newCode}</button></>;
  const expired = left === 0;
  return <div className="link-layout">
    <div className={"qr-frame" + (expired ? " expired" : "")}><QrCode value={challenge.challenge} label={m.link.qrLabel} /></div>
    <div className="link-steps">
      <ol><li>{m.link.step1}</li><li>{m.link.step2}</li><li>{m.link.step3}</li></ol>
      <label>{m.link.code}<span className="code-field"><input readOnly dir="ltr" value={challenge.challenge} onFocus={(event) => event.currentTarget.select()} /><button className="quiet-button" type="button" onClick={() => copy(challenge.challenge)}><Icon name="copy" size={18} />{copied ? m.link.copied : m.link.copy}</button></span></label>
      {expired
        ? <p className="status error" role="alert">{m.link.expired} <button className="text-button" type="button" onClick={() => void create(replace)}>{m.link.newCode}</button></p>
        : <p className="countdown" aria-live="off">{formatMessage(m.link.expiresIn, { time: "" })}<bdi dir="ltr" className="data-text">{text}</bdi></p>}
    </div>
  </div>;
}

export function LinkPhoneDialog({ deviceId, name, token, open, onOpenChange }: { deviceId: string; name: string; token: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const { messages: m } = useI18n();
  return <Dialog.Root open={open} onOpenChange={onOpenChange}>
    <Dialog.Portal>
      <Dialog.Overlay className="dialog-overlay" />
      <Dialog.Content className="dialog link-dialog" aria-describedby={undefined}>
        <div className="dialog-heading"><Dialog.Title>{m.link.title.split("{name}")[0]}<bdi>{name}</bdi>{m.link.title.split("{name}")[1]}</Dialog.Title><Dialog.Close asChild><button className="icon-button" type="button" aria-label={m.common.close}><Icon name="close" /></button></Dialog.Close></div>
        {/* Mounting only while open means every opening asks for a fresh code. */}
        {open && <LinkBody deviceId={deviceId} token={token} />}
        <div className="dialog-actions"><Dialog.Close asChild><button className="quiet-button" type="button">{m.link.done}</button></Dialog.Close></div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
