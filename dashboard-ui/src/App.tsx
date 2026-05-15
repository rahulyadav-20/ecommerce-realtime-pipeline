import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout      from "@/components/Layout";
import FunnelPage  from "@/pages/Funnel";
import LiveEvents  from "@/pages/LiveEvents";
import Overview    from "@/pages/Overview";
import SystemHealth from "@/pages/SystemHealth";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="funnel" element={<FunnelPage />} />
          <Route path="events" element={<LiveEvents />} />
          <Route path="health" element={<SystemHealth />} />
          {/* Redirect unknown paths to overview */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
