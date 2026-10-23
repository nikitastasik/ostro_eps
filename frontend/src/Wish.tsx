import { useState } from "react";
import { api } from "./api";
import { Confidence, ErrorBox, Icon, NeedIcon, Sources, Spinner, useAsync, useToast } from "./ui";

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // запасной путь для браузеров без доступа к буферу обмена
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  }
}

function WishModal({ hotelId, q, onClose }: { hotelId: string; q: string; onClose: () => void }) {
  const w = useAsync(() => api.wish(hotelId, q), [hotelId, q]);
  const toast = useToast();
  const d = w.data;
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal card" role="dialog" aria-label="Пожелание к бронированию" onClick={(e) => e.stopPropagation()}>
        <div className="row between">
          <h2 style={{ margin: 0 }}>Пожелание к бронированию</h2>
          <button className="btn small" onClick={onClose}><Icon name="x" size={14} /> Закрыть</button>
        </div>
        <ErrorBox error={w.error} />
        {w.loading && <Spinner />}
        {d && (
          <>
            <p className="small muted mt-s">
              {d.hotel.name} · запрос «{d.query}» · {d.method === "llm" ? "формулировки отшлифовал LLM, факты сверены с данными" : "текст собран из данных агентов, без LLM"}
            </p>
            <div className="wish-text">{d.text}</div>
            <div className="row mt-s">
              <button className="btn primary" onClick={async () => toast.show((await copyText(d.text)) ? "Текст скопирован — вставьте его в комментарий к бронированию" : "Не удалось скопировать")}>
                <Icon name="check" size={15} /> Скопировать
              </button>
              {d.share_text && <span className="small muted">{d.share_text}</span>}
            </div>
            <h3 className="mt">Почему так</h3>
            <div className="needs">
              {d.reasons.map((r) => (
                <div className="need" key={r.title}>
                  <NeedIcon status={r.status} />
                  <div><b>{r.title}:</b> {r.text}</div>
                  <div className="row" style={{ gap: 6 }}><Sources sources={r.sources} /><Confidence value={r.confidence} label={false} /></div>
                </div>
              ))}
            </div>
            {d.questions.length > 0 && (
              <p className="small muted mt-s">Вопросы в тексте — о том, чего в данных пока нет: агент тоже спросил об этом отельера.</p>
            )}
          </>
        )}
        {toast.node}
      </div>
    </div>
  );
}

export function WishButton({ hotelId, q, small }: { hotelId: string; q: string; small?: boolean }) {
  const [open, setOpen] = useState(false);
  if (!q) return null;
  return (
    <>
      <button className={`btn ${small ? "small" : ""}`} onClick={() => setOpen(true)} title="Готовый текст для отеля: какой номер просить и почему">
        <Icon name="chat" size={small ? 14 : 16} /> Пожелание к бронированию
      </button>
      {open && <WishModal hotelId={hotelId} q={q} onClose={() => setOpen(false)} />}
    </>
  );
}
