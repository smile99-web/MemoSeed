"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { register, saveAuthSession, validateEmail, validatePassword, validateUsername } from "@/lib/auth";

interface RegisterFormState {
  email: string;
  username: string;
  password: string;
  confirmPassword: string;
}

interface RegisterFormErrors {
  email?: string;
  username?: string;
  password?: string;
  confirmPassword?: string;
  form?: string;
}

export default function RegisterPage() {
  const router = useRouter();
  const [form, setForm] = useState<RegisterFormState>({
    email: "",
    username: "",
    password: "",
    confirmPassword: "",
  });
  const [errors, setErrors] = useState<RegisterFormErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const nextErrors: RegisterFormErrors = {
      email: validateEmail(form.email) ?? undefined,
      username: validateUsername(form.username) ?? undefined,
      password: validatePassword(form.password) ?? undefined,
      confirmPassword: form.password === form.confirmPassword ? undefined : "两次输入的密码不一致",
    };

    if (nextErrors.email || nextErrors.username || nextErrors.password || nextErrors.confirmPassword) {
      setErrors(nextErrors);
      return;
    }

    setIsSubmitting(true);
    setErrors({});

    try {
      const auth = await register({
        email: form.email.trim(),
        username: form.username.trim(),
        password: form.password,
      });
      saveAuthSession(auth);
      router.push("/");
    } catch (error) {
      setErrors({ form: error instanceof Error ? error.message : "注册失败" });
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-10 ipad:px-8">
      <Card className="w-full max-w-md ipad:max-w-lg">
        <CardHeader>
          <CardTitle className="ipad:text-2xl">注册 MemoSeed</CardTitle>
          <CardDescription className="ipad:text-lg">创建账号，开始建立英语长期记忆。</CardDescription>
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
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring ipad:h-12 ipad:px-4 ipad:text-base"
                value={form.email}
                onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
              />
              {errors.email ? <p className="text-sm text-red-600 ipad:text-base">{errors.email}</p> : null}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium ipad:text-base" htmlFor="username">
                用户名
              </label>
              <input
                id="username"
                type="text"
                autoComplete="username"
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring ipad:h-12 ipad:px-4 ipad:text-base"
                value={form.username}
                onChange={(event) => setForm((current) => ({ ...current, username: event.target.value }))}
              />
              {errors.username ? <p className="text-sm text-red-600 ipad:text-base">{errors.username}</p> : null}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium ipad:text-base" htmlFor="password">
                密码
              </label>
              <input
                id="password"
                type="password"
                autoComplete="new-password"
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring ipad:h-12 ipad:px-4 ipad:text-base"
                value={form.password}
                onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))}
              />
              {errors.password ? <p className="text-sm text-red-600 ipad:text-base">{errors.password}</p> : null}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium ipad:text-base" htmlFor="confirm-password">
                确认密码
              </label>
              <input
                id="confirm-password"
                type="password"
                autoComplete="new-password"
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring ipad:h-12 ipad:px-4 ipad:text-base"
                value={form.confirmPassword}
                onChange={(event) => setForm((current) => ({ ...current, confirmPassword: event.target.value }))}
              />
              {errors.confirmPassword ? <p className="text-sm text-red-600 ipad:text-base">{errors.confirmPassword}</p> : null}
            </div>

            {errors.form ? <p className="text-sm text-red-600 ipad:text-base">{errors.form}</p> : null}

            <Button className="w-full ipad:h-12 ipad:text-lg" disabled={isSubmitting} type="submit">
              {isSubmitting ? "注册中..." : "注册"}
            </Button>
          </form>

          <p className="mt-6 text-center text-sm text-muted-foreground ipad:text-base">
            已有账号？{" "}
            <Link className="font-medium text-primary hover:underline" href="/login">
              去登录
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
