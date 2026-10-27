import { Router, type IRouter } from "express";
import {
  AskSupportAssistantBody,
  AskSupportAssistantResponse,
  ListSupportTicketsResponse,
  SearchKnowledgeBaseResponse,
} from "@workspace/api-zod";
import { openai } from "@workspace/integrations-openai-ai-server";

const router: IRouter = Router();
const pythonBaseUrl = process.env.PYTHON_SERVICE_URL ?? "http://127.0.0.1:8031";

router.post("/support/chat", async (req, res): Promise<void> => {
  const parsed = AskSupportAssistantBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  try {
    let ticketContext = "";
    if (parsed.data.ticket_id) {
      const ticketUrl = new URL("/api/support/tickets", pythonBaseUrl);
      ticketUrl.searchParams.set("search", parsed.data.ticket_id);
      ticketUrl.searchParams.set("limit", "10");
      const ticketResponse = await fetch(ticketUrl);
      if (ticketResponse.ok) {
        const ticketData = ListSupportTicketsResponse.safeParse(
          await ticketResponse.json(),
        );
        const ticket = ticketData.success
          ? ticketData.data.find((item) => item.ticket_id === parsed.data.ticket_id)
          : undefined;
        if (ticket) {
          ticketContext = `\nTicket context: ${ticket.customer_message}`;
        }
      }
    }

    const searchResponse = await fetch(
      new URL("/api/support/search", pythonBaseUrl),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: `${parsed.data.message}${ticketContext}`,
          limit: 3,
        }),
      },
    );
    if (!searchResponse.ok) {
      req.log.error(
        { status: searchResponse.status },
        "Knowledge retrieval failed before assistant generation",
      );
      res.status(502).json({ error: "Knowledge retrieval is unavailable." });
      return;
    }

    const search = SearchKnowledgeBaseResponse.safeParse(
      await searchResponse.json(),
    );
    if (!search.success) {
      req.log.error(
        { errors: search.error.message },
        "Knowledge retrieval returned an invalid response",
      );
      res.status(502).json({ error: "Knowledge retrieval returned invalid data." });
      return;
    }

    const citations = search.data.results.map((result) => ({
      article_id: result.article_id,
      title: result.article_title,
      category: result.category,
      excerpt: result.text,
    }));
    const context = citations
      .map(
        (citation, index) =>
          `[${index + 1}] ${citation.title} (${citation.category}, ${citation.article_id})\n${citation.excerpt}`,
      )
      .join("\n\n");

    const completion = await openai.chat.completions.create({
      model: "gpt-5.6-luna",
      max_completion_tokens: 8192,
      messages: [
        {
          role: "system",
          content:
            "You are a customer-support agent assistant. Give a concise, empathetic, practical suggested reply in the same language as the customer's message (Arabic or English). Use Modern Standard Arabic for Arabic replies. Use only the supplied knowledge-base evidence for policy claims. If the evidence does not answer the question, say what is unknown and recommend escalation. Do not invent refunds, timelines, or account actions. Do not include citation numbers in the prose; sources are shown separately.",
        },
        {
          role: "user",
          content: `Customer question: ${parsed.data.message}${ticketContext}\n\nKnowledge-base evidence:\n${context || "No relevant knowledge articles were found."}`,
        },
      ],
    });
    const answer =
      completion.choices[0]?.message?.content?.trim() ||
      (/[\u0600-\u06FF]/.test(parsed.data.message)
        ? "تعذّر إنشاء اقتراح موثّق. يُرجى مراجعة المقالات المستشهد بها أو تصعيد الحالة."
        : "I couldn't generate a grounded suggestion. Please review the cited articles or escalate.");
    const topScore = search.data.results[0]?.rerank_score ?? 0;
    const confidence = citations.length
      ? Math.max(0.45, Math.min(0.94, 0.55 + topScore * 1.5))
      : 0;
    const response = AskSupportAssistantResponse.parse({
      answer,
      citations,
      model: "gpt-5.6-luna",
      confidence,
    });
    res.json(response);
  } catch (error) {
    req.log.error({ err: error }, "Support assistant request failed");
    res.status(502).json({
      error: "The support assistant could not complete this request.",
    });
  }
});

router.use("/support", async (req, res): Promise<void> => {
  try {
    const response = await fetch(
      `${pythonBaseUrl}${req.originalUrl}`,
      {
        method: req.method,
        headers: {
          "Content-Type": "application/json",
          ...(req.headers.accept ? { Accept: req.headers.accept } : {}),
        },
        ...(req.method === "GET" || req.method === "HEAD"
          ? {}
          : { body: JSON.stringify(req.body ?? {}) }),
      },
    );
    res.status(response.status);
    const contentType = response.headers.get("content-type");
    if (contentType) res.setHeader("Content-Type", contentType);
    res.send(await response.text());
  } catch (error) {
    req.log.error({ err: error }, "Support API sidecar request failed");
    res.status(503).json({
      error: "Support processing is temporarily unavailable.",
    });
  }
});

export default router;
