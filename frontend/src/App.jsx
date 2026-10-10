import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth'
import Layout from './components/Layout'
import { LiveProvider } from './live'
import Complaints from './pages/Complaints'
import Cycles from './pages/Cycles'
import ComponentDetail from './pages/ComponentDetail'
import ElevatorDetail from './pages/ElevatorDetail'
import Elevators from './pages/Elevators'
import Energy from './pages/Energy'
import Login from './pages/Login'
import ModelCard from './pages/ModelCard'
import Operational from './pages/Operational'
import Recommendations from './pages/Recommendations'
import ReportIssue from './pages/ReportIssue'
import Sustainability from './pages/Sustainability'
import MaterialSpec from './pages/MaterialSpec'
import Materials from './pages/Materials'
import LiftReport from './pages/LiftReport'
import Sensors from './pages/Sensors'
import SensorInputList from './pages/SensorInputList'
import TractionLiftSetup from './pages/TractionLiftSetup'
import TractionLiftConfigure from './pages/TractionLiftConfigure'
import TractionLiftSummary from './pages/TractionLiftSummary'

// Elevator data is only visible to logged-in admin/staff; the complaint form is public.
function Protected({ children }) {
  const { user, ready } = useAuth()
  const location = useLocation()
  if (!ready) return null
  if (!user) return <Navigate to="/login" state={{ from: location.pathname }} replace />
  return <LiveProvider>{children}</LiveProvider>
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/report" element={<ReportIssue />} />
          <Route path="/report/:elevatorId" element={<ReportIssue />} />
          <Route element={<Protected><Layout /></Protected>}>
            <Route path="/" element={<Navigate to="/elevators" replace />} />
            <Route path="/elevators" element={<Elevators />} />
            <Route path="/new-lift" element={<TractionLiftSetup />} />
            <Route path="/lift/:id" element={<TractionLiftConfigure />} />
            <Route path="/lift/:id/summary" element={<TractionLiftSummary />} />
            <Route path="/elevators/:id" element={<ElevatorDetail />} />
            <Route path="/elevators/:id/operational" element={<Operational />} />
            <Route path="/elevators/:id/materials" element={<MaterialSpec />} />
            <Route path="/materials" element={<Materials />} />
            <Route path="/elevators/:id/report" element={<LiftReport />} />
            <Route path="/elevators/:id/sensors" element={<Sensors />} />
            <Route path="/sensors" element={<SensorInputList />} />
            <Route path="/elevators/:id/cycles" element={<Cycles />} />
            <Route path="/cycles" element={<Cycles />} />
            <Route path="/recommendations" element={<Recommendations />} />
            <Route path="/components/:id" element={<ComponentDetail />} />
            <Route path="/energy" element={<Energy />} />
            <Route path="/sustainability" element={<Sustainability />} />
            <Route path="/complaints" element={<Complaints />} />
            <Route path="/model" element={<ModelCard />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
