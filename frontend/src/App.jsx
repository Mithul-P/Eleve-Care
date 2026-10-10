import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth'
import Layout from './components/Layout'
import { LiveProvider } from './live'
import Complaints from './pages/Complaints'
import ComponentDetail from './pages/ComponentDetail'
import ElevatorDetail from './pages/ElevatorDetail'
import Elevators from './pages/Elevators'
import Energy from './pages/Energy'
import Login from './pages/Login'
import ModelCard from './pages/ModelCard'
import ReportIssue from './pages/ReportIssue'
import Sustainability from './pages/Sustainability'

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
            <Route path="/elevators/:id" element={<ElevatorDetail />} />
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
