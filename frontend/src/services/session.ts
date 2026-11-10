import type { User } from "./types";

const KEY = "socboard.session";
type Session = { token: string; user: User };
type Listener = (s: Session | null) => void;

let current: Session | null = null;
let loaded = false;
const listeners = new Set<Listener>();

function load() {
  if (loaded || typeof window === "undefined") return;
  loaded = true;
  try {
    const raw = window.sessionStorage.getItem(KEY);
    current = raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    current = null;
  }
}

export const session = {
  get(): Session | null {
    load();
    return current;
  },
  set(s: Session | null) {
    current = s;
    loaded = true;
    if (typeof window !== "undefined") {
      if (s) window.sessionStorage.setItem(KEY, JSON.stringify(s));
      else window.sessionStorage.removeItem(KEY);
    }
    listeners.forEach((l) => l(s));
  },
  subscribe(l: Listener) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};
