import React, { Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

// The reviewer portal is a separate chunk: reviewers never download the admin app code.
const isPortal = /^\/review\/?$/.test(window.location.pathname) || new URLSearchParams(window.location.search).get("mode") === "ptsl";
const Screen = lazy(() => (isPortal ? import("./Portal.jsx") : import("./App.jsx")));

createRoot(document.getElementById("root")).render(
  <Suspense fallback={<div className="app"><div className="empty">Loading…</div></div>}>
    <Screen />
  </Suspense>
);
