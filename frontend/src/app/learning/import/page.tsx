"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getAccessToken } from "@/lib/auth";
import { LearningImportResponse, uploadLearningItems, validateImportFile } from "@/lib/learning";

export default function LearningImportPage() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [result, setResult] = useState<LearningImportResponse | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    setSelectedFile(file);
    setResult(null);
    setErrorMessage(validateImportFile(file));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const validationMessage = validateImportFile(selectedFile);
    if (validationMessage) {
      setErrorMessage(validationMessage);
      return;
    }

    const file = selectedFile;
    if (!file) {
      setErrorMessage("请选择 TXT 或 Excel 文件");
      return;
    }

    const accessToken = getAccessToken();
    if (!accessToken) {
      setErrorMessage("请先登录后再导入学习内容");
      return;
    }

    setIsUploading(true);
    setErrorMessage(null);
    setResult(null);

    try {
      const response = await uploadLearningItems(file, accessToken);
      setResult(response);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "导入失败");
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <main className="min-h-screen px-6 py-10">
      <section className="mx-auto max-w-4xl space-y-6">
        <div>
          <Link className="text-sm font-medium text-primary hover:underline" href="/">
            返回首页
          </Link>
          <h1 className="mt-4 text-3xl font-bold tracking-tight">导入学习内容</h1>
          <p className="mt-2 text-muted-foreground">支持 TXT 和 Excel 文件，系统会自动分类为单词、短语或句子，并按账号去重。</p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>上传文件</CardTitle>
            <CardDescription>
              TXT 每行格式建议：英文,中文。Excel 建议列：english_text、chinese_text、item_type、phonetic、difficulty_level。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-5" onSubmit={handleSubmit}>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="learning-file">
                  TXT / Excel 文件
                </label>
                <input
                  id="learning-file"
                  type="file"
                  accept=".txt,.xlsx"
                  className="block w-full rounded-md border border-input bg-background px-3 py-2 text-sm file:mr-4 file:rounded-md file:border-0 file:bg-primary file:px-4 file:py-2 file:text-sm file:font-medium file:text-primary-foreground"
                  onChange={handleFileChange}
                />
              </div>

              {selectedFile ? <p className="text-sm text-muted-foreground">已选择：{selectedFile.name}</p> : null}
              {errorMessage ? <p className="text-sm text-red-600">{errorMessage}</p> : null}

              <Button disabled={isUploading} type="submit">
                {isUploading ? "导入中..." : "开始导入"}
              </Button>
            </form>
          </CardContent>
        </Card>

        {result ? (
          <Card>
            <CardHeader>
              <CardTitle>导入结果</CardTitle>
              <CardDescription>
                共读取 {result.total_rows} 行，成功导入 {result.imported_count} 条，跳过 {result.skipped_count} 条。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="grid gap-3 md:grid-cols-3">
                <div className="rounded-lg bg-primary/10 p-4">
                  <p className="text-sm text-muted-foreground">成功导入</p>
                  <p className="text-2xl font-bold text-primary">{result.imported_count}</p>
                </div>
                <div className="rounded-lg bg-secondary p-4">
                  <p className="text-sm text-muted-foreground">跳过</p>
                  <p className="text-2xl font-bold">{result.skipped_count}</p>
                </div>
                <div className="rounded-lg bg-secondary p-4">
                  <p className="text-sm text-muted-foreground">总行数</p>
                  <p className="text-2xl font-bold">{result.total_rows}</p>
                </div>
              </div>

              {result.items.length > 0 ? (
                <div className="space-y-3">
                  <h2 className="font-semibold">新增内容</h2>
                  <div className="overflow-hidden rounded-lg border">
                    <table className="w-full text-left text-sm">
                      <thead className="bg-muted">
                        <tr>
                          <th className="px-3 py-2">类型</th>
                          <th className="px-3 py-2">英文</th>
                          <th className="px-3 py-2">中文</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.items.slice(0, 20).map((item) => (
                          <tr className="border-t" key={item.id}>
                            <td className="px-3 py-2">{item.item_type}</td>
                            <td className="px-3 py-2">{item.english_text}</td>
                            <td className="px-3 py-2">{item.chinese_text}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : null}

              {result.skipped_items.length > 0 ? (
                <div className="space-y-3">
                  <h2 className="font-semibold">跳过内容</h2>
                  <ul className="space-y-2 text-sm text-muted-foreground">
                    {result.skipped_items.slice(0, 20).map((item, index) => (
                      <li className="rounded-md border px-3 py-2" key={`${item.english_text}-${index}`}>
                        {item.english_text || "空行"}：{item.reason}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </CardContent>
          </Card>
        ) : null}
      </section>
    </main>
  );
}
