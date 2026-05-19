import { Brain, CalendarClock, GraduationCap, Repeat2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const workflowCards = [
  {
    title: "热身复习",
    description: "昨天错题、高遗忘风险、即将到期内容。",
    minutes: "10 分钟",
    icon: Repeat2,
  },
  {
    title: "新内容学习",
    description: "新单词与常用短语，控制每日认知负荷。",
    minutes: "20 分钟",
    icon: GraduationCap,
  },
  {
    title: "句子训练",
    description: "中译英、拼写、填空，强化主动回忆。",
    minutes: "20 分钟",
    icon: Brain,
  },
  {
    title: "错题强化",
    description: "今日错误当天再次出现，拼写重新输入。",
    minutes: "10 分钟",
    icon: CalendarClock,
  },
] as const;

export default function HomePage() {
  return (
    <main className="min-h-screen px-6 py-10">
      <section className="mx-auto flex max-w-6xl flex-col gap-10">
        <div className="rounded-3xl bg-white p-8 shadow-sm md:p-12">
          <p className="text-sm font-semibold uppercase tracking-[0.3em] text-primary">MemoSeed</p>
          <h1 className="mt-4 max-w-3xl text-4xl font-bold tracking-tight md:text-6xl">
            儿童英语长期记忆操作系统
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-8 text-muted-foreground">
            基于艾宾浩斯记忆曲线、SM-2 和 AI 动态调度，专注小学阶段英语基础薄弱学生的单词、短语与简单句长期记忆。
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Button asChild>
              <a href="/login">开始学习</a>
            </Button>
            <Button asChild variant="outline">
              <a href="/register">创建账号</a>
            </Button>
            <Button asChild variant="secondary">
              <a href="/learning/import">导入内容</a>
            </Button>
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {workflowCards.map((card) => {
            const Icon = card.icon;
            return (
              <Card key={card.title}>
                <CardHeader>
                  <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
                    <Icon className="h-5 w-5" />
                  </div>
                  <CardTitle>{card.title}</CardTitle>
                  <CardDescription>{card.minutes}</CardDescription>
                </CardHeader>
                <CardContent>
                  <p className="text-sm leading-6 text-muted-foreground">{card.description}</p>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </section>
    </main>
  );
}
