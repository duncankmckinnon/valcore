import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import OverviewPage from "./pages/OverviewPage";
import EvaluatorsPage from "./pages/EvaluatorsPage";
import DatasetsPage from "./pages/DatasetsPage";
import RunsPage from "./pages/RunsPage";

// All routes are declared here up front. The route table is maintained alongside the
// pages, so adding or moving a route means editing this file.
export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<OverviewPage />} />
        <Route path="/evaluators" element={<EvaluatorsPage />} />
        <Route path="/evaluators/:id" element={<EvaluatorsPage />} />
        <Route path="/datasets" element={<DatasetsPage />} />
        <Route path="/datasets/:id" element={<DatasetsPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/compare" element={<RunsPage />} />
        <Route path="/runs/:id" element={<RunsPage />} />
      </Route>
    </Routes>
  );
}
