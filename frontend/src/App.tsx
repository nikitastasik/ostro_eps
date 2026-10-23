import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, Meta } from "./api";
import AgentsPage from "./pages/AgentsPage";
import CharacteristicsPage from "./pages/CharacteristicsPage";
import ComparePage from "./pages/ComparePage";
import HotelierPage from "./pages/HotelierPage";
import HotelPage from "./pages/HotelPage";
import HotelsPage from "./pages/HotelsPage";
import MetricsPage from "./pages/MetricsPage";
import SearchPage from "./pages/SearchPage";
import { Icon, plural, useAsync, useCompare } from "./ui";

function CompareBar() {
  const cmp = useCompare();
  const nav = useNavigate();
  if (cmp.items.length === 0) return null;
  return (
    <div className="cmp-bar">
      <span className="small">Сравнение: {cmp.items.map((x) => x.name).join(", ").slice(0, 70)}</span>
      <button className="btn small primary" disabled={cmp.items.length < 2}
        onClick={() => nav(`/compare?ids=${cmp.items.map((x) => x.id).join(",")}${cmp.query ? `&q=${encodeURIComponent(cmp.query)}` : ""}`)}>
        Сравнить ({cmp.items.length})
      </button>
      <button className="btn small" onClick={cmp.clear}>Очистить</button>
    </div>
  );
}

export default function App() {
  const meta = useAsync<Meta>(() => api.meta(), []);
  const m = meta.data;
  return (
    <>
      <header className="topbar">
        <NavLink to="/" className="brand">
          <span className="brand-logo"><Icon name="compass" size={18} stroke={2.2} /></span>
          <span>Островок · Знания об отелях<small>{m ? `${m.region.name} · ${m.hotels} ${plural(m.hotels, "отель", "отеля", "отелей")} · ${m.characteristics} ${plural(m.characteristics, "характеристика", "характеристики", "характеристик")}` : "загрузка…"}</small></span>
        </NavLink>
        <nav className="nav">
          <NavLink to="/" end>Поиск</NavLink>
          <NavLink to="/hotels">Отели</NavLink>
          <NavLink to="/compare">Сравнение</NavLink>
          <NavLink to="/hotelier">Кабинет отельера</NavLink>
          <NavLink to="/agents">Агенты</NavLink>
          <NavLink to="/characteristics">Характеристики</NavLink>
          <NavLink to="/metrics">Метрики</NavLink>
        </nav>
        <div className="spacer" />
        {m && (
          <span className={`chip ${m.llm.enabled ? "good" : "ghost"}`} title={m.llm.enabled ? `Модель ${m.llm.model}` : "LLM не подключена: запросы и ответы разбираются правилами. Добавьте LLM_API_KEY в .env"}>
            <Icon name="bolt" size={14} /> <span className="llm-text">{m.llm.enabled ? `LLM: ${m.llm.model}` : "LLM выкл · правила"}</span>
          </span>
        )}
      </header>
      <Routes>
        <Route path="/" element={<SearchPage meta={m} />} />
        <Route path="/hotels" element={<HotelsPage />} />
        <Route path="/hotel/:id" element={<HotelPage />} />
        <Route path="/compare" element={<ComparePage />} />
        <Route path="/hotelier" element={<HotelierPage />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/characteristics" element={<CharacteristicsPage />} />
        <Route path="/metrics" element={<MetricsPage />} />
        <Route path="*" element={<div className="page"><div className="empty">Страница не найдена</div></div>} />
      </Routes>
      <CompareBar />
    </>
  );
}
