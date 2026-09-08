import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@carbon/styles/css/styles.css";
import "./styles.css";
import StudioRoot from "./StudioRoot";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StudioRoot />
  </StrictMode>,
);
