import { AuthForm } from "@/components/AuthForm";

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <AuthForm mode="login" />
    </main>
  );
}
