"use client";

/* App shell: 240px sidebar (collapsible), top bar with month switcher + bell,
   bottom tab bar on mobile (Section 11.2). */
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  BarChart3, Bell, CalendarDays, ChevronsLeft, FilePlus2, History as HistoryIcon,
  LayoutDashboard, LogOut, Menu, PenLine, Settings as SettingsIcon, Sparkles,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { http } from "@/lib/api";
import type { NotificationItem } from "@/lib/types";
import { fmtDate } from "@/components/ui";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/create", label: "Create Post", icon: FilePlus2 },
  { href: "/calendar", label: "Calendar", icon: CalendarDays },
  { href: "/plans", label: "Plans", icon: PenLine },
  { href: "/history", label: "History", icon: HistoryIcon },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/settings", label: "Settings", icon: SettingsIcon },
];

const MOBILE_NAV = ["/dashboard", "/create", "/calendar", "/history", "/analytics"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout, loading } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const [bellOpen, setBellOpen] = useState(false);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);

  useEffect(() => {
    if (!loading && !user && !pathname.startsWith("/login") && !pathname.startsWith("/register")) {
      router.replace("/login");
    }
  }, [loading, user, pathname, router]);

  const loadNotifications = async () => {
    try {
      setNotifications(await http.get<NotificationItem[]>("/notifications"));
    } catch {
      /* unauthenticated - the redirect above handles it */
    }
  };

  useEffect(() => {
    if (user) void loadNotifications();
  }, [user]); // eslint-disable-line react-hooks/exhaustive-deps

  const unread = notifications.filter((n) => !n.read_at).length;
  const monthLabel = new Date().toLocaleDateString("en-GB", { month: "long", year: "numeric" });

  const markRead = async (id: string) => {
    try {
      await http.post(`/notifications/${id}/read`);
      void loadNotifications();
    } catch { /* ignore */ }
  };

  if (!user) {
    return <div className="min-h-screen bg-canvas">{children}</div>;
  }

  return (
    <div className="min-h-screen bg-canvas">
      {/* sidebar */}
      <aside
        className={`hidden md:flex fixed inset-y-0 left-0 z-30 flex-col border-r border-line bg-surface
          transition-all duration-200 ${collapsed ? "w-16" : "w-60"}`}
      >
        <div className="h-14 flex items-center justify-between px-4 border-b border-line">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-7 h-7 rounded-lg bg-primary-600 text-white flex items-center justify-center shrink-0">
              <Sparkles className="w-4 h-4" />
            </div>
            {!collapsed && <span className="font-semibold text-ink truncate">MonthlyMuse</span>}
          </div>
          {!collapsed && (
            <button className="btn-ghost btn-sm !px-2" onClick={() => setCollapsed(true)} aria-label="Collapse sidebar">
              <ChevronsLeft className="w-4 h-4" />
            </button>
          )}
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto" aria-label="Main">
          {NAV.map((item) => {
            const active = pathname === item.href || pathname.startsWith(item.href + "/");
            const Icon = item.icon;
            return (
              <a
                key={item.href}
                href={item.href}
                className={`nav-link ${active ? "nav-link-active" : ""} ${collapsed ? "justify-center px-0" : ""}`}
                title={collapsed ? item.label : undefined}
              >
                <Icon className="w-5 h-5 shrink-0" />
                {!collapsed && <span>{item.label}</span>}
              </a>
            );
          })}
        </nav>

        <div className="p-3 border-t border-line">
          <div className={`flex items-center gap-3 ${collapsed ? "justify-center" : ""}`}>
            <div className="w-8 h-8 rounded-full bg-primary-50 text-primary-700 flex items-center justify-center text-small font-semibold shrink-0">
              {(user.full_name ?? user.email)[0].toUpperCase()}
            </div>
            {!collapsed && (
              <div className="min-w-0 flex-1">
                <p className="text-small text-ink truncate">{user.full_name ?? user.email}</p>
                <p className="text-small text-muted truncate">{user.email}</p>
              </div>
            )}
            {!collapsed && (
              <button className="btn-ghost btn-sm !px-2" onClick={() => void logout()} aria-label="Log out" title="Log out">
                <LogOut className="w-4 h-4" />
              </button>
            )}
          </div>
          {collapsed && (
            <button className="btn-ghost btn-sm w-full mt-2 !px-2" onClick={() => setCollapsed(false)} aria-label="Expand sidebar">
              <Menu className="w-4 h-4" />
            </button>
          )}
        </div>
      </aside>

      {/* main column */}
      <div className={`transition-all duration-200 ${collapsed ? "md:pl-16" : "md:pl-60"}`}>
        <header className="sticky top-0 z-20 h-14 bg-surface/90 backdrop-blur border-b border-line flex items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <div className="md:hidden w-7 h-7 rounded-lg bg-primary-600 text-white flex items-center justify-center">
              <Sparkles className="w-4 h-4" />
            </div>
            <span className="text-body font-medium text-ink">{monthLabel}</span>
          </div>

          <div className="flex items-center gap-2">
            <div className="relative">
              <button
                className="btn-ghost btn-sm relative !px-2.5"
                onClick={() => setBellOpen((o) => !o)}
                aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`}
              >
                <Bell className="w-5 h-5" />
                {unread > 0 && (
                  <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-danger text-white text-[10px] flex items-center justify-center font-semibold">
                    {unread}
                  </span>
                )}
              </button>
              {bellOpen && (
                <div className="absolute right-0 mt-2 w-80 card shadow-lift z-30 animate-fade">
                  <div className="px-4 py-3 border-b border-line flex items-center justify-between">
                    <span className="text-small font-semibold text-ink">Notifications</span>
                    <span className="text-small text-muted">{unread} unread</span>
                  </div>
                  <div className="max-h-80 overflow-y-auto">
                    {notifications.length === 0 && (
                      <p className="px-4 py-6 text-body text-muted text-center">Nothing yet.</p>
                    )}
                    {notifications.map((n) => (
                      <button
                        key={n.id}
                        className="w-full text-left px-4 py-3 border-b border-line last:border-0 hover:bg-canvas transition-colors"
                        onClick={() => {
                          void markRead(n.id);
                          if (n.cycle_id) router.push("/review");
                        }}
                      >
                        <p className={`text-body ${n.read_at ? "text-muted" : "text-ink font-medium"}`}>{n.title}</p>
                        {n.body && <p className="text-small text-muted mt-0.5 line-clamp-2">{n.body}</p>}
                        <p className="text-small text-muted mt-1">{fmtDate(n.created_at)}</p>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <button className="btn-primary btn-sm" onClick={() => router.push("/create")}>
              <PenLine className="w-4 h-4" /> New post
            </button>
          </div>
        </header>

        <main className="max-w-content mx-auto px-4 sm:px-6 py-6 pb-24 md:pb-10">{children}</main>
      </div>

      {/* mobile bottom tabs */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 z-30 bg-surface border-t border-line flex" aria-label="Mobile">
        {NAV.filter((n) => MOBILE_NAV.includes(n.href)).map((item) => {
          const active = pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <a
              key={item.href}
              href={item.href}
              className={`flex-1 flex flex-col items-center justify-center gap-0.5 h-14 text-small transition-colors
                ${active ? "text-primary-700 font-medium" : "text-muted"}`}
            >
              <Icon className="w-5 h-5" />
              {item.label.split(" ")[0]}
            </a>
          );
        })}
      </nav>
    </div>
  );
}
