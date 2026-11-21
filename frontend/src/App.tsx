import { ThemeProvider } from '@figma/astraui'
import { AuthGate, AuthProvider } from './AuthWorkspace'
import { P1Workspace } from './P1Workspace'

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <AuthGate>
          <P1Workspace />
        </AuthGate>
      </AuthProvider>
    </ThemeProvider>
  )
}
