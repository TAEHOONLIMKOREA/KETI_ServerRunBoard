import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { Loading } from "./components/Status";
import Admin from "./pages/Admin";
import Dashboard from "./pages/Dashboard";
import Gpus from "./pages/Gpus";
import NodeDetail from "./pages/NodeDetail";
import UserDetail from "./pages/UserDetail";
import Users from "./pages/Users";
import WorkloadDetail from "./pages/WorkloadDetail";

// 메뉴얼은 README 본문과 markdown 변환기를 따로 묶어, 대시보드를 처음 열 때 받는 파일에 싣지 않는다
const Manual = lazy(() => import("./pages/Manual"));

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
        <Route
          path="/manual"
          element={
            <Suspense fallback={<Loading />}>
              <Manual />
            </Suspense>
          }
        />
        <Route path="/admin" element={<Admin />} />
        <Route path="*" element={<div className="empty">페이지가 없습니다.</div>} />
      </Route>
    </Routes>
  );
}
