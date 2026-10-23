import { useState } from "react";
import { Link } from "react-router-dom";
import { api, TourData } from "../api";
import { ErrorBox, Icon, Spinner, useAsync, useToast } from "../ui";

interface Step {
  title: string;
  todo: string;
  look: string;
  req: string[];
  href: string;
  extra?: { href: string; label: string };
}

function steps(d: TourData): Step[] {
  const q = encodeURIComponent(d.query);
  const fh = d.floor_hotel;
  const ch = d.conflict_hotel;
  const sh = d.scenario_hotel;
  const eh = d.evidence_hotel;
  const hotel = (id: string, tail = "") => `/hotel/${encodeURIComponent(id)}${tail}`;
  const out: Step[] = [
    {
      title: "Главный запрос: что подходит и с каким условием",
      todo: `Откройте выдачу по запросу «${d.query}». Посмотрите, как агент разобрал запрос на пожелания, у кого «✓ с условием» и почему отель выше соседнего. В карточке нажмите «Пожелание к бронированию».`,
      look: "Совет «номер с окнами на … сторону, не ниже N-го этажа», доля подходящих номеров, значки ✓ / ✓ с условием / ~ / ✕ / ? с источником и уверенностью.",
      req: ["Основное 5: поиск на естественном языке", "Доп. 1: объяснение выбора"],
      href: `/?q=${q}`,
    },
  ];
  if (fh) {
    out.push({
      title: "Что видно из окна: панорама и лучи на карте",
      todo: `Откройте «${fh.name}»: панорама уже на рекомендованной стороне${fh.min_floor ? ` и ${fh.min_floor}-м этаже` : ""}. Двигайте ползунок этажа вниз и вверх и смотрите на карту лучей под панорамой; выберите месяц заката.`,
      look: "Красные контуры — дома, которые закрывают вид; подпись «Из 120° обзора: …° — море, …° — дом в N м от окна»; оранжевый пунктир — где садится солнце.",
      req: ["Основное 1: характеристика по геоданным"],
      href: hotel(fh.id, `?q=${q}#view`),
    });
  }
  if (eh) {
    out.push({
      title: "Достоверность значения",
      todo: `Откройте «${eh.name}»: характеристика «Вид на море» уже раскрыта — видно, из каких фактов она собрана и откуда уверенность.`,
      look: `${eh.multi ? "Расчёт по геоданным и ответ отельера сведены в одно значение; у" : "У"} каждого факта — источник, уверенность, дата наблюдения, срок годности и доказательства (этажность, доля зданий с известной высотой).`,
      req: ["Основное 3: источник, уверенность и дата у каждого значения"],
      href: hotel(eh.id, "#char-sea_view"),
    });
  }
  if (fh) {
    out.push({
      title: "Агенты работают вживую",
      todo: "Нажмите «Пересчитать агентами» вверху карточки отеля.",
      look: "Журнал: план по рецептам → какие инструменты применимы к этому отелю и почему → результаты с уверенностью → решения арбитра → вопросы отельеру.",
      req: ["Основное 1: агент сам выбирает источники и инструменты"],
      href: hotel(fh.id),
    });
  }
  out.push({
    title: "Кабинет отельера",
    todo: "Вход уже заполнен: hotelier / hotelier123. Ответьте на вопрос агента своими словами и нажмите «Как агент поймёт ответ?», затем «Отправить ответ».",
    look: "Ответ раскладывается по полям; блок «источники расходятся» с уточняющим вопросом агента; «Что поднимет вас в выдаче» — вопросы по пользе для спроса.",
    req: ["Основное 2: диалог с отельером", "Основное 3: противоречия", "Доп. 3: кабинет отельера"],
    href: ch ? `/hotelier?hotel=${encodeURIComponent(ch.id)}` : "/hotelier",
  });
  out.push({
    title: "Новая характеристика текстом",
    todo: `Описание «${d.characteristic}» уже вписано. Нажмите «Скомпилировать», затем «Создать и запустить агентов по всем отелям».`,
    look: `Рецепт агента (инструмент OSM, радиус, фильтр «круглосуточно»); агенты собирают значение для всех ${d.hotels} отелей за секунды, и характеристика появляется в поиске.`,
    req: ["Основное 4: расширяемость без программирования"],
    href: `/characteristics?add=${encodeURIComponent(d.characteristic)}`,
  });
  if (sh) {
    out.push({
      title: "Рядом построили дом",
      todo: sh.built
        ? `Дом перед окнами «${sh.name}» уже построен. Нажмите «Лучи этого отеля»: карта откроется на стороне ${sh.facade}, перед которой он стоит. Снести дом — кнопка «Снести всё построенное» на странице агентов или «Вернуть демо» внизу.`
        : `На странице агентов нажмите «Построить» — дом встанет перед «морскими» окнами «${sh.name}». Затем нажмите «Лучи этого отеля»: карта откроется на той стороне${sh.facade ? ` (${sh.facade})` : ""}, перед которой построен дом.`,
      look: "Агенты пересчитывают соседей, лента изменений показывает «было → стало»; на карте лучей новый дом закрывает часть обзора.",
      req: ["Доп. 4: актуализация данных"],
      href: `/agents?scenario=${encodeURIComponent(sh.id)}#scenario`,
      extra: { href: hotel(sh.id, `${sh.facade ? `?facade=${encodeURIComponent(sh.facade)}&floor=${sh.floor || 1}` : ""}#view`), label: "Лучи этого отеля" },
    });
  }
  if (d.top.length >= 2) {
    out.push({
      title: "Сравнение под запрос",
      todo: "Откройте сравнение трёх лучших отелей главного запроса. Включите «Только различия» и раскройте строку кликом.",
      look: "Первым — пожелания запроса со статусами у каждого отеля и победителем строки, итоговая оценка и совет по номеру; ниже — короткие значения всех характеристик.",
      req: ["Доп. 2: сравнение"],
      href: `/compare?ids=${d.top.map(encodeURIComponent).join(",")}&q=${q}`,
    });
  }
  out.push({
    title: "Метрики качества",
    todo: "Откройте метрики.",
    look: "Покрытие, доля вопросов отельерам, точность разбора на отложенных выборках, согласие ранжирования с разметкой, польза вопросов, сверка Солнца с PyEphem.",
    req: ["Доп. 5: метрики качества"],
    href: "/metrics",
  });
  return out;
}

export default function TourPage() {
  const t = useAsync<TourData>(() => api.tour(), []);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const reset = async () => {
    if (!window.confirm("Вернуть демо в исходное состояние? Ответы, добавленные характеристики и построенные дома будут удалены.")) return;
    setBusy(true);
    try {
      await api.demoReset();
      toast.show("Демо возвращено в исходное состояние");
      t.reload();
    } catch (e) {
      toast.show(String(e));
    }
    setBusy(false);
  };
  return (
    <div className="page">
      <h1>Проверить за 5 минут</h1>
      <p className="muted">
        Девять шагов по всем требованиям задания. Каждая кнопка открывает нужное состояние; отели для показа подобраны по текущим данным
        (с открытым противоречием, где этаж меняет вид и т. д.). Подробный сценарий — в <span className="mono">docs/DEMO.md</span>.
      </p>
      <ErrorBox error={t.error} />
      {t.loading && <Spinner />}
      {t.data && steps(t.data).map((s, i) => (
        <div className="card tour-step" key={s.title}>
          <div className="tour-n">{i + 1}</div>
          <div className="col" style={{ gap: 6, minWidth: 0 }}>
            <h3 style={{ margin: 0 }}>{s.title}</h3>
            <div className="small"><b>Что сделать.</b> {s.todo}</div>
            <div className="small"><b>На что смотреть.</b> {s.look}</div>
            <div className="row wrap">{s.req.map((r) => <span key={r} className="chip accent">{r}</span>)}</div>
          </div>
          <div className="col tour-actions">
            <Link className="btn primary" to={s.href}>Открыть <Icon name="search" size={14} /></Link>
            {s.extra && <Link className="btn small" to={s.extra.href}>{s.extra.label}</Link>}
          </div>
        </div>
      ))}
      <div className="card mt">
        <h3>Вернуть демо в исходное состояние</h3>
        <p className="small muted">Удаляет ответы, добавленные характеристики и построенные дома, заново загружает знания агентов из сида. Фоновые запуски на это время приостанавливаются.</p>
        <button className="btn" disabled={busy} onClick={reset}>{busy ? <Spinner /> : <Icon name="refresh" size={15} />} Вернуть демо</button>
      </div>
      {toast.node}
    </div>
  );
}
