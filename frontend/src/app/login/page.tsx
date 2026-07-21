"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { login, saveAuthSession, validateEmail, validatePassword } from "@/lib/auth";

interface LoginFormState {
  email: string;
  password: string;
}

interface LoginFormErrors {
  email?: string;
  password?: string;
  form?: string;
}

export default function LoginPage() {
  const router = useRouter();
  const [form, setForm] = useState<LoginFormState>({ email: "", password: "" });
  const [errors, setErrors] = useState<LoginFormErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const nextErrors: LoginFormErrors = {
      email: validateEmail(form.email) ?? undefined,
      password: validatePassword(form.password) ?? undefined,
    };

    if (nextErrors.email || nextErrors.password) {
      setErrors(nextErrors);
      return;
    }

    setIsSubmitting(true);
    setErrors({});

    try {
      const auth = await login({ email: form.email.trim(), password: form.password });
      saveAuthSession(auth);
      router.push("/");
    } catch (error) {
      setErrors({ form: error instanceof Error ? error.message : "登录失败" });
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-10 ipad:px-8">
      <Card className="animate-pop-in w-full max-w-md ipad:max-w-lg">
        <CardHeader>
          <div className="icon-chip mb-4 h-12 w-12 animate-float rounded-2xl text-2xl">🌱</div>
          <CardTitle className="ipad:text-2xl">
            登录 <span className="text-gradient">MemoSeed</span>
          </CardTitle>
          <CardDescription className="ipad:text-lg">进入你的英语长期记忆学习系统。</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-5" onSubmit={handleSubmit} noValidate>
            <div className="space-y-2">
              <label className="text-sm font-medium ipad:text-base" htmlFor="email">
                邮箱
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                className="input-tech h-10 w-full rounded-xl border border-input bg-white/70 px-3 text-sm outline-none focus:border-cyan-400 ipad:h-12 ipad:px-4 ipad:text-base"
                value={form.email}
                onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
              />
              {errors.email ? <p className="text-sm text-red-600">{errors.email}</p> : null}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium ipad:text-base" htmlFor="password">
                密码
              </label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                className="input-tech h-10 w-full rounded-xl border border-input bg-white/70 px-3 text-sm outline-none focus:border-cyan-400 ipad:h-12 ipad:px-4 ipad:text-base"
                value={form.password}
                onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))}
              />
              {errors.password ? <p className="text-sm text-red-600">{errors.password}</p> : null}
            </div>

            {errors.form ? <p className="text-sm text-red-600">{errors.form}</p> : null}

            <Button className="w-full ipad:h-12 ipad:text-lg" disabled={isSubmitting} type="submit">
              {isSubmitting ? "登录中..." : "🚀 登录，开始学习"}
            </Button>
          </form>

          <p className="mt-6 text-center text-sm text-muted-foreground ipad:text-base">
            还没有账号？{" "}
            <Link className="font-medium text-primary hover:underline" href="/register">
              立即注册
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
