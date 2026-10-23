import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, CharValue, getToken, HotelCard, Question, setToken } from "../api";
import { Steps } from "../RunLog";
import { Confidence, ErrorBox, fmtDate, Icon, Sources, Spinner, StatusBadge, useAsync, useToast } from "../ui";

function Login({ onLogin }: { onLogin: () => void }) {
  const [login, setLogin] = useState("hotelier");
  const [password, setPassword] = useState("hotelier123");
  const [error, setError] = useState<unknown>(null);
  return (
    <div className="page" style={{ maxWidth: 460 }}>
      <div className="card">
        <h1>Кабинет отельера</h1>
        <p className="muted small">Отельер видит, что система узнала о его отеле, подтверждает или исправляет значения и отвечает на вопросы агентов. Демо-доступ ко всем отелям: <b>hotelier / hotelier123</b>.</p>
        <form className="col mt" onSubmit={async (e) => {
          e.preventDefault();
          try {
            const r = await api.login(login, password);
            setToken(r.token);
            onLogin();
          } catch (err) { setError(err); }
        }}>
          <input className="input" value={login} onChange={(e) => setLogin(e.target.value)} placeholder="Логин" />
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Пароль" />
          <button className="btn primary">Войти</button>
          <ErrorBox error={error} />
        </form>
      </div>
    </div>
  );
}

function QuestionCard({ q, onDone }: { q: Question; onDone: () => void }) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [steps, setSteps] = useState<any[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const doPreview = async () => {
    setBusy(true);
    try { setPreview((await api.answer(q.id, text, true)).preview); setError(null); } catch (e) { setError(e); }
    setBusy(false);
  };
  const submit = async () => {
    setBusy(true);
    try { const r = await api.answer(q.id, text); setSteps(r.run.steps); onDone(); } catch (e) { setError(e); }
    setBusy(false);
  };
  const simulate = async () => {
    setBusy(true);
    try { const r = await api.simulate(q.id); setSteps(r.run.steps || []); onDone(); } catch (e) { setError(e); }
    setBusy(false);
  };
  return (
    <div className="qcard open" id={`q-${q.id}`}>
      <div className="row between">
        <span className="row" style={{ gap: 6 }}>
          <span className="badge asked">{q.reason === "conflict" ? "уточнение" : q.reason === "verify" ? "подтвердите расчёт" : q.reason === "low_confidence" ? "агент не уверен" : "нет в открытых данных"}</span>
          {(q.titles || []).map((t) => <span key={t} className="chip ghost">{t}</span>)}
        </span>
        <span className="tiny muted">{fmtDate(q.created_at, true)}</span>
      </div>
      <p className="mt-s">{q.text}</p>
      {q.utility && <div className="small utility">{q.utility.score > 0 ? "↑ " : ""}{q.utility.text}</div>}
      {!steps && (
        <>
          <textarea className="textarea" placeholder="Ответьте своими словами — агент сам разложит ответ по полям" value={text} onChange={(e) => { setText(e.target.value); setPreview(null); }} />
          <div className="row mt-s wrap">
            <button className="btn small" disabled={!text.trim() || busy} onClick={doPreview}>Как агент поймёт ответ?</button>
            <button className="btn small primary" disabled={!text.trim() || busy} onClick={submit}>Отправить ответ</button>
            <button className="btn small ghost" disabled={busy} onClick={simulate} title="Сгенерировать ответ от имени отельера по скрытой «правде» отеля">Ответить за отельера (симуляция)</button>
            {busy && <Spinner />}
          </div>
        </>
      )}
      <ErrorBox error={error} />
      {preview && Object.entries(preview).map(([k, v]: [string, any]) => (
        <div key={k} className="notice small mt-s">
          <b>Агент понял ({v.method === "llm" ? "LLM" : "правила"}):</b> {v.summary || "ничего не удалось извлечь"}
          <div className="mono tiny mt-s">{JSON.stringify(v.fields)}</div>
          {v.missing?.length > 0 && <div className="tiny">Не извлечено: {v.missing.join(", ")} — можно дописать в ответ</div>}
        </div>
      ))}
      {steps && <div className="mt-s"><b className="small">Что сделали агенты:</b><Steps steps={steps} /></div>}
    </div>
  );
}

function KnownItem({ c, hotelId, onChanged }: { c: CharValue; hotelId: string; onChanged: () => void }) {
  const [edit, setEdit] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  return (
    <div className="char">
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div>
          <div className="row" style={{ gap: 8 }}><b>{c.title}</b><StatusBadge status={c.status} /></div>
          <div className={`small ${c.summary ? "" : "muted"}`}>{c.summary || "нет данных"}</div>
          <div className="char-meta"><Sources sources={c.sources} />{c.status !== "unknown" && c.status !== "asked" && <Confidence value={c.confidence} />}</div>
        </div>
        <div className="row">
          {c.value && <button className="btn small good" disabled={busy} onClick={async () => {
            setBusy(true);
            try { await api.confirm(hotelId, c.key); toast.show("Спасибо! Значение подтверждено"); onChanged(); } catch (e) { toast.show(String(e)); }
            setBusy(false);
          }}><Icon name="check" size={14} /> Верно</button>}
          <button className="btn small" onClick={() => setEdit(!edit)}><Icon name="edit" size={14} /> Исправить</button>
        </div>
      </div>
      {edit && (
        <div className="mt-s">
          <textarea className="textarea" placeholder="Опишите, как на самом деле — свободным текстом" value={text} onChange={(e) => setText(e.target.value)} />
          <button className="btn small primary mt-s" disabled={!text.trim() || busy} onClick={async () => {
            setBusy(true);
            try { const r = await api.correct(hotelId, c.key, text); toast.show(`Агент записал: ${r.value.summary}`); setEdit(false); onChanged(); } catch (e) { toast.show(String(e)); }
            setBusy(false);
          }}>Сохранить</button>
        </div>
      )}
      {toast.node}
    </div>
  );
}

export default function HotelierPage() {
  const [params, setParams] = useSearchParams();
  const [authed, setAuthed] = useState(!!getToken());
  const [q, setQ] = useState("");
  const hotelId = params.get("hotel") || "";
  const list = useAsync<{ items: any[] }>(() => (authed ? api.hotelierHotels() : Promise.resolve({ items: [] })), [authed]);
  const card = useAsync<HotelCard | null>(() => (authed && hotelId ? api.hotelierHotel(hotelId) : Promise.resolve(null)), [authed, hotelId]);
  const [tab, setTab] = useState<"questions" | "known">("questions");

  useEffect(() => {
    const err = (list.error || card.error) as { status?: number } | null;
    if (err && err.status === 401) { setToken(null); setAuthed(false); }
  }, [list.error, card.error]);

  if (!authed) return <Login onLogin={() => setAuthed(true)} />;
  const items = (list.data?.items || []).filter((h) => !q || h.name.toLowerCase().includes(q.toLowerCase()));
  const h = card.data;
  const open = (h?.questions || []).filter((x) => x.status === "open");
  const answered = (h?.questions || []).filter((x) => x.status !== "open");

  return (
    <div className="page wide">
      <div className="row between mb">
        <h1>Кабинет отельера</h1>
        <button className="btn small ghost" onClick={() => { setToken(null); setAuthed(false); }}>Выйти</button>
      </div>
      <div className="layout-hotelier">
        <div className="card" style={{ padding: 12 }}>
          <input className="input" placeholder="Найти свой отель" value={q} onChange={(e) => setQ(e.target.value)} />
          <div style={{ maxHeight: 640, overflow: "auto", marginTop: 8 }}>
            {list.loading && <Spinner />}
            {items.map((x) => (
              <div key={x.id} onClick={() => setParams({ hotel: x.id })} style={{ padding: "8px 10px", borderRadius: 8, cursor: "pointer", background: x.id === hotelId ? "var(--accent-soft)" : undefined }}>
                <div className="row between"><span className="small"><b>{x.name}</b></span>{x.open_questions > 0 && <span className="badge asked">{x.open_questions}</span>}</div>
                <div className="tiny muted">{x.address || ""}</div>
              </div>
            ))}
          </div>
        </div>
        <div>
          {!hotelId && <div className="card empty">Выберите отель слева — отельер увидит вопросы агентов и всё, что система о нём знает.</div>}
          <ErrorBox error={card.error} />
          {hotelId && card.loading && !h && <Spinner />}
          {h && (
            <>
              <div className="card">
                <div className="row between wrap">
                  <div>
                    <h2>{h.name}</h2>
                    <div className="small muted">{h.address ? `${h.address} · ` : ""}<Link to={`/hotel/${encodeURIComponent(h.id)}`}>как видят гости</Link></div>
                  </div>
                  <div className="row">
                    <span className="chip violet">вопросов: {open.length}</span>
                    <span className="chip bad">противоречий: {(h.conflicts || []).length}</span>
                    <span className="chip accent">знаем {h.known} из {h.total}</span>
                  </div>
                </div>
                {(h.boosts || []).length > 0 && (
                  <div className="boosts mt">
                    <h3>Что поднимет вас в выдаче</h3>
                    <p className="tiny muted">Польза ответа считается по спросу: журнал поисков гостей и демо-запросы (примеры с главной и размеченные выборки). Вопросы ниже отсортированы так же.</p>
                    {h.boosts!.map((b) => (
                      <div key={b.question_id} className="row between mt-s boost">
                        <div className="small"><b>{(b.titles || []).join(", ")}</b> — {b.text}</div>
                        <button className="btn small nowrap" onClick={() => { setTab("questions"); setTimeout(() => document.getElementById(`q-${b.question_id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 50); }}>Ответить</button>
                      </div>
                    ))}
                  </div>
                )}
                <div className="grid two mt">
                  <div>
                    <h3>Ваши сильные стороны</h3>
                    {(h.strengths || []).length === 0 && <div className="small muted">Пока не выделяются — ответьте на вопросы агентов</div>}
                    {(h.strengths || []).map((s) => (
                      <div key={s.key} className="small mt-s"><span className="badge ok">лучше {s.percentile}% отелей района</span> <b>{s.title}</b><div className="muted">{s.summary}</div></div>
                    ))}
                  </div>
                  <div>
                    <h3>По каким запросам вас находят</h3>
                    {h.search_appearances && h.search_appearances.count > 0 ? (
                      <>
                        <div className="small muted">в топ-10 в {h.search_appearances.count} из {h.search_appearances.searches} последних поисков</div>
                        {h.search_appearances.items.map((s, i) => <div key={i} className="small mt-s">#{s.rank} · «{s.query}»</div>)}
                      </>
                    ) : <div className="small muted">В последних поисках отель не попадал в топ-10</div>}
                  </div>
                </div>
              </div>

              {(h.conflicts || []).length > 0 && (
                <div className="card">
                  <h3>Нужна ваша помощь: источники расходятся</h3>
                  {(h.conflicts || []).map((c) => (
                    <div key={c.id} className="qcard">
                      <b className="small">{c.title}</b>
                      <div className="small">{c.description}</div>
                      {c.facts.map((f) => <div key={f.id} className="tiny muted mt-s"><span className={`src ${f.source}`}>{f.source_ru}</span> {f.summary}</div>)}
                    </div>
                  ))}
                </div>
              )}

              <div className="card">
                <div className="tabs">
                  <button className={tab === "questions" ? "on" : ""} onClick={() => setTab("questions")}>Вопросы агентов ({open.length})</button>
                  <button className={tab === "known" ? "on" : ""} onClick={() => setTab("known")}>Что о вас знает система</button>
                </div>
                {tab === "questions" && (
                  <>
                    {open.length === 0 && <div className="empty small">Новых вопросов нет</div>}
                    {open.map((x) => <QuestionCard key={x.id} q={x} onDone={() => card.reload()} />)}
                    {answered.length > 0 && <h3 className="mt">Отвеченные</h3>}
                    {answered.slice(0, 10).map((x) => (
                      <div key={x.id} className="qcard">
                        <div className="small">{x.text}</div>
                        <div className="small mt-s"><b>Ответ:</b> {x.answer_text}</div>
                        {x.parsed && Object.entries(x.parsed).map(([k, v]: [string, any]) => <div key={k} className="tiny mono muted mt-s">{JSON.stringify(v.fields)}</div>)}
                      </div>
                    ))}
                  </>
                )}
                {tab === "known" && h.groups.map((g) => (
                  <div key={g.key} className="mb">
                    <h3>{g.title}</h3>
                    {g.items.map((c) => <KnownItem key={c.key} c={c} hotelId={h.id} onChanged={() => card.reload()} />)}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
