import type { User } from "./types";

const KEY = "socboard.session";
type Session = { token: string; user: User };
type Listener = (s: Session | null) => void;

let current: Session | null = null;
let loaded = false;
const listeners = new Set<Listener>();
// Why the last session ended, when the server ended it (a 401): shown on Sign in. Memory only,
// never in session or local storage; cleared by the next `set` (a sign-in or a plain log out).
let endedMessage: string | null = null;

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

function store(s: Session | null) {
  current = s;
  loaded = true;
  if (typeof window !== "undefined") {
    if (s) window.sessionStorage.setItem(KEY, JSON.stringify(s));
    else window.sessionStorage.removeItem(KEY);
  }
  listeners.forEach((l) => l(s));
}

export const session = {
  get(): Session | null {
    load();
    return current;
  },
  set(s: Session | null) {
    endedMessage = null;
    store(s);
  },
  /** End the session because the server refused its token (a 401), keeping the server's message. */
  expire(message: string) {
    endedMessage = message;
    store(null);
  },
  /** The server's message from the 401 that ended the last session, or null. */
  endedMessage(): string | null {
    return endedMessage;
  },
  subscribe(l: Listener) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};
