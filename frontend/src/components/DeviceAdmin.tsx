import { FormEvent, useState } from "react";
import { send } from "../api";
import { useI18n } from "../i18n";
import { Icon } from "./Brand";
import { LinkPhoneDialog } from "./LinkPhone";

function suggestedId() {
  const bytes = crypto.getRandomValues(new Uint8Array(3));
  return "phone-" + Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function AddDevice({ token, added }: { token: string; added: (device: { id: string; name: string }) => void }) {
  const { messages: m } = useI18n();
  const [open, setOpen] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const [deviceId, setDeviceId] = useState(suggestedId);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError("");
    try {
      const device = await send<{ id: string; name: string }>("/devices", { name: String(form.get("name") || "").trim(), external_device_id: String(form.get("deviceId") || "").trim(), platform: "android" }, { token });
      setOpen(false); setDeviceId(suggestedId()); added(device);
    } catch { setError(m.link.createFailed); } finally { setBusy(false); }
  }
  if (!open) return <button type="button" className="with-icon" onClick={() => setOpen(true)}><Icon name="plus" size={18} />{m.link.addDevice}</button>;
  return <form className="panel add-device" onSubmit={(event) => void submit(event)} aria-labelledby="add-device-title">
    <h2 id="add-device-title">{m.link.addTitle}</h2>
    {error && <p className="status error" role="alert">{error}</p>}
    <div className="field-row">
      <label>{m.link.name}<input name="name" placeholder={m.link.namePlaceholder} maxLength={120} required autoFocus /></label>
      <label>{m.link.deviceId}<input name="deviceId" dir="ltr" value={deviceId} onChange={(event) => setDeviceId(event.target.value)} maxLength={128} required aria-describedby="device-id-hint" /></label>
    </div>
    <p className="hint" id="device-id-hint">{m.link.deviceIdHint}</p>
    <div className="button-row"><button disabled={busy} type="submit">{m.link.create}</button><button className="quiet-button" type="button" onClick={() => setOpen(false)}>{m.link.cancel}</button></div>
  </form>;
}

export function DeviceActions({ id, name, status, token, changed, linkOpen, setLinkOpen }: {
  id: string; name: string; status: string; token: string; changed: () => void; linkOpen: boolean; setLinkOpen: (open: boolean) => void;
}) {
  const { messages: m } = useI18n();
  const [confirming, setConfirming] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function setStatus(next: "DISABLED" | "PENDING") {
    setBusy(true); setError("");
    try { await send(`/devices/${id}`, { status: next }, { token, method: "PATCH" }); setConfirming(false); changed(); }
    catch { setError(m.link.actionFailed); } finally { setBusy(false); }
  }
  if (status === "DISABLED") return <div className="device-actions">
    <p className="hint">{m.link.disabledNote}</p>
    <button className="quiet-button" type="button" disabled={busy} onClick={() => void setStatus("PENDING")}>{m.link.enable}</button>
    {error && <p className="status error" role="alert">{error}</p>}
  </div>;
  return <div className="device-actions">
    <button type="button" className="with-icon" onClick={() => setLinkOpen(true)}><Icon name="link" size={18} />{m.link.linkPhone}</button>
    {confirming
      ? <div className="confirm-row" role="group" aria-label={m.link.disable}><span>{m.link.disableConfirm}</span><button className="danger-button" type="button" disabled={busy} onClick={() => void setStatus("DISABLED")}>{m.link.confirmDisable}</button><button className="quiet-button" type="button" onClick={() => setConfirming(false)}>{m.link.cancel}</button></div>
      : <button className="text-button danger" type="button" onClick={() => setConfirming(true)}>{m.link.disable}</button>}
    {error && <p className="status error" role="alert">{error}</p>}
    <LinkPhoneDialog deviceId={id} name={name} token={token} open={linkOpen} onOpenChange={(open) => { setLinkOpen(open); if (!open) changed(); }} />
  </div>;
}
