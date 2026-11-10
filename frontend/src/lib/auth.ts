import { useEffect, useState } from "react";
import { session } from "@/services";

/** Returns [hydrated, session]. Session lives in sessionStorage, so it is only known on the client. */
export function useSession() {
  const [hydrated, setHydrated] = useState(false);
  const [s, setS] = useState<ReturnType<typeof session.get>>(null);
  useEffect(() => {
    setS(session.get());
    setHydrated(true);
    const unsub = session.subscribe(setS);
    return () => {
      unsub();
    };
  }, []);
  return { hydrated, session: s };
}
