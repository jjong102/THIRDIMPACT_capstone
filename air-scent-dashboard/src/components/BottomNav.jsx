import { Home, LayoutGrid } from "lucide-react";

import "./BottomNav.css";

const navItems = [
  {
    id: "home",
    label: "홈",
    icon: Home,
  },
  {
    id: "more",
    label: "더보기",
    icon: LayoutGrid,
  },
];

function BottomNav({ activePage, onChangePage }) {
  return (
    <nav className="bottom-nav">
      {navItems.map((item) => {
        const Icon = item.icon;
        const isActive = activePage === item.id;

        return (
          <button
            key={item.id}
            type="button"
            className={`bottom-nav-button ${isActive ? "active" : ""}`}
            onClick={() => onChangePage(item.id)}
          >
            <Icon size={24} strokeWidth={2.4} />
            <span>{item.label}</span>
          </button>
        );
      })}
    </nav>
  );
}

export default BottomNav;
