import { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";
import { session } from "./services";

/** Empties the cache when the session ends (log out and any 401 both set it to null). Returns the unsubscribe. */
export const clearCacheOnLogout = (queryClient: QueryClient) =>
  session.subscribe((s) => {
    if (!s) queryClient.clear();
  });

export const getRouter = () => {
  const queryClient = new QueryClient();
  // shortcut: never unsubscribed; the app calls getRouter once (main.tsx), tests build their own routers.
  clearCacheOnLogout(queryClient);

  const router = createRouter({
    routeTree,
    context: { queryClient },
    scrollRestoration: true,
    // Lets Query own freshness; set caching defaults on the QueryClient, not the router.
    defaultPreloadStaleTime: 0,
  });

  return router;
};

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof getRouter>;
  }
}
