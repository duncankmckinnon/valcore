import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { startApp } from "./bootstrap";
import { initializeFrontendTelemetry } from "./frontendTelemetry";
import "./styles.css";

void startApp(
  () => {
    ReactDOM.createRoot(document.getElementById("root")!).render(
      <React.StrictMode>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </React.StrictMode>,
    );
  },
  initializeFrontendTelemetry,
);
