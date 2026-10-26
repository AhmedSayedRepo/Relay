import * as Dialog from "@radix-ui/react-dialog";
import { useMemo, useState } from "react";
import { getHelpTopic, getHelpTopics } from "../help/topics";
import { useI18n } from "../i18n";

export function HelpDialog({ topicId = "dashboard-overview" }: { topicId?: string }) {
  const { locale, messages: m } = useI18n();
  const [selected, setSelected] = useState(topicId);
  const [search, setSearch] = useState("");
  const topics = useMemo(() => getHelpTopics(locale), [locale]);
  const topic = getHelpTopic(locale, selected);
  const results = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase(locale);
    return needle ? topics.filter((item) => (item.title + " " + item.summary).toLocaleLowerCase(locale).includes(needle)) : topics;
  }, [locale, search, topics]);
  return <Dialog.Root onOpenChange={(open) => { if (open) setSelected(topicId); }}>
    <Dialog.Trigger asChild><button className="quiet-button help-trigger" type="button"><span aria-hidden="true">?</span>{m.common.help}</button></Dialog.Trigger>
    <Dialog.Portal>
      <Dialog.Overlay className="dialog-overlay" />
      <Dialog.Content className="dialog" aria-describedby="help-summary">
        <div className="dialog-heading"><Dialog.Title>{m.helpUi.title}</Dialog.Title><Dialog.Close asChild><button className="icon-button" type="button" aria-label={m.common.close}>×</button></Dialog.Close></div>
        <label className="search-label">{m.helpUi.search}<input value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        <div className="help-layout">
          <nav aria-label={m.helpUi.topics}><ul>{results.map((item) => <li key={item.id}><button className={item.id === topic.id ? "selected" : ""} onClick={() => setSelected(item.id)} type="button">{item.title}</button></li>)}</ul>{results.length === 0 && <p className="muted">{m.helpUi.noMatch}</p>}</nav>
          <article>
            <p className="eyebrow">{m.helpUi.topic} · <bdi dir="ltr">{topic.id}</bdi></p>
            <h2>{topic.title}</h2><p id="help-summary">{topic.summary}</p>
            <ol>{topic.steps.map((step) => <li key={step}>{step}</li>)}</ol>
            <h3>{m.helpUi.related}</h3><div className="related-links">{topic.related.map((id) => <button type="button" key={id} onClick={() => setSelected(id)}>{getHelpTopic(locale, id).title}</button>)}</div>
          </article>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
