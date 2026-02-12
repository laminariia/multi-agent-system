import { json } from "@remix-run/node";
import {
  Link,
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
  useLoaderData,
  useRouteError,
  isRouteErrorResponse,
} from "@remix-run/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Toaster } from "~/components/ui/toaster";
import { useThemeStore } from "~/stores/theme-store";

import "~/tailwind.css";

export async function loader() {
  return json({
    ENV: {
      API_URL: process.env.API_URL ?? "",
    },
  });
}

export function Layout({ children }: { children: React.ReactNode }) {
  // Inline script to apply saved theme BEFORE first paint (prevents flash).
  // Reads the zustand persisted store from localStorage.
  const themeScript = `(function(){try{var t=JSON.parse(localStorage.getItem("theme-storage")||"{}");document.documentElement.className=t.state&&t.state.theme||"dark"}catch(e){document.documentElement.className="dark"}})()`;

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <Meta />
        <Links />
      </head>
      <body className="min-h-screen bg-background text-foreground antialiased">
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

function ThemeHydration() {
  const theme = useThemeStore((s) => s.theme);

  useEffect(() => {
    document.documentElement.className = theme;
  }, [theme]);

  return null;
}

export function ErrorBoundary() {
  const error = useRouteError();
  const isResponse = isRouteErrorResponse(error);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="text-center space-y-4">
        <p className="text-7xl font-bold text-muted-foreground/30">
          {isResponse ? error.status : "Error"}
        </p>
        <h1 className="text-2xl font-semibold text-foreground">
          {isResponse ? error.statusText : "Something went wrong"}
        </h1>
        <p className="text-muted-foreground text-sm max-w-sm mx-auto">
          {isResponse
            ? "The page you are looking for does not exist or has been moved."
            : "An unexpected error occurred. Please try again."}
        </p>
        <Link
          to="/dashboard"
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          Back to Dashboard
        </Link>
      </div>
    </div>
  );
}

export default function App() {
  const data = useLoaderData<typeof loader>();
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      })
  );

  return (
    <QueryClientProvider client={queryClient}>
      <script
        dangerouslySetInnerHTML={{
          __html: `window.ENV = ${JSON.stringify(data.ENV)}`,
        }}
      />
      <ThemeHydration />
      <Outlet />
      <Toaster />
    </QueryClientProvider>
  );
}
