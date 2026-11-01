import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Admin from "./pages/Admin";
import Dashboard from "./pages/Dashboard";
import Gpus from "./pages/Gpus";
import NodeDetail from "./pages/NodeDetail";
import UserDetail from "./pages/UserDetail";
import Users from "./pages/Users";
import WorkloadDetail from "./pages/WorkloadDetail";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/gpus" element={<Gpus />} />
        <Route path="/nodes/:name" element={<NodeDetail />} />
        <Route path="/users" element={<Users />} />
        <Route path="/users/:username" element={<UserDetail />} />
        <Route path="/workloads/:id" element={<WorkloadDetail />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="*" element={<div className="empty">페이지가 없습니다.</div>} />
      </Route>
    </Routes>
  );
}
