import { useEffect } from "react";
import { Outlet, RouterProvider, createBrowserRouter, useLocation } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { extensionRoutes } from "./extensions/registry";
import { AppLayout } from "./layouts/AppLayout";
import { AuthLayout } from "./layouts/AuthLayout";
import { PublicLayout } from "./layouts/PublicLayout";
import { DashboardPage } from "./pages/Dashboard";
import { DocsPage } from "./pages/Docs";
import { LandingPage } from "./pages/Landing";
import { PrivacyPage, SecurityPage, TermsPage } from "./pages/Legal";
import { LoginPage } from "./pages/Login";
import { NotFoundPage } from "./pages/NotFound";
import { ProjectDetailPage } from "./pages/ProjectDetail";
import { ProjectsPage } from "./pages/Projects";
import { RegisterPage } from "./pages/Register";

/** Scrolls to the hash target on navigation, or to the top when there is none. */
function ScrollManager() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    if (hash) {
      document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView();
    } else {
      window.scrollTo(0, 0);
    }
  }, [pathname, hash]);
  return null;
}

function RootLayout() {
  return (
    <>
      <ScrollManager />
      <Outlet />
    </>
  );
}

const router = createBrowserRouter([
  {
    element: <RootLayout />,
    children: [
      {
        element: <PublicLayout />,
        children: [
          { path: "/", element: <LandingPage /> },
          { path: "/docs", element: <DocsPage /> },
          { path: "/security", element: <SecurityPage /> },
          { path: "/privacy", element: <PrivacyPage /> },
          { path: "/terms", element: <TermsPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
      {
        element: <AuthLayout />,
        children: [
          { path: "/login", element: <LoginPage /> },
          { path: "/register", element: <RegisterPage /> },
        ],
      },
      {
        path: "/app",
        element: <RequireAuth />,
        children: [
          {
            element: <AppLayout />,
            children: [
              { index: true, element: <DashboardPage /> },
              { path: "projects", element: <ProjectsPage /> },
              { path: "projects/:projectId", element: <ProjectDetailPage /> },
              ...extensionRoutes,
            ],
          },
        ],
      },
    ],
  },
]);

export function App() {
  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  );
}
