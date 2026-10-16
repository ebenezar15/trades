import { lazy, Suspense } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Loading } from './components/ui';
import { AppProvider } from './lib/app-context';
import { AuthGate } from './lib/auth';
import Dashboard from './pages/Dashboard';

const Signals = lazy(() => import('./pages/Signals'));
const OptionChain = lazy(() => import('./pages/OptionChain'));
const Context = lazy(() => import('./pages/Context'));
const News = lazy(() => import('./pages/News'));
const Trades = lazy(() => import('./pages/Trades'));
const Analytics = lazy(() => import('./pages/Analytics'));
const StrategyLab = lazy(() => import('./pages/StrategyLab'));
const Replay = lazy(() => import('./pages/Replay'));
const Learn = lazy(() => import('./pages/Learn'));
const Premarket = lazy(() => import('./pages/Premarket'));
const Eod = lazy(() => import('./pages/Eod'));
const Settings = lazy(() => import('./pages/Settings'));
const Health = lazy(() => import('./pages/Health'));
const Agent = lazy(() => import('./pages/Agent'));
const Connect = lazy(() => import('./pages/Connect'));

export default function App() {
  return (
    <BrowserRouter>
      <AuthGate>
      <AppProvider>
        <Layout>
          <Suspense fallback={<Loading />}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/signals" element={<Signals />} />
              <Route path="/option-chain" element={<OptionChain />} />
              <Route path="/context" element={<Context />} />
              <Route path="/news" element={<News />} />
              <Route path="/trades" element={<Trades />} />
              <Route path="/analytics" element={<Analytics />} />
              <Route path="/strategies" element={<StrategyLab />} />
              <Route path="/replay" element={<Replay />} />
              <Route path="/learn" element={<Learn />} />
              <Route path="/premarket" element={<Premarket />} />
              <Route path="/eod" element={<Eod />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/health" element={<Health />} />
              <Route path="/agent" element={<Agent />} />
              <Route path="/connect" element={<Connect />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </Layout>
      </AppProvider>
      </AuthGate>
    </BrowserRouter>
  );
}
