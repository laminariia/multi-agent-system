import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "~/components/ui/card";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Separator } from "~/components/ui/separator";
import { Badge } from "~/components/ui/badge";
import { useAuthStore } from "~/stores/auth-store";
import { toast } from "~/hooks/use-toast";

export default function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const [telegramCode, setTelegramCode] = useState("");
  const [linkingTelegram, setLinkingTelegram] = useState(false);

  const handleTelegramLink = async () => {
    if (!telegramCode.trim() || telegramCode.length !== 6) {
      toast({
        title: "Invalid code",
        description: "Please enter the 6-character code from the Telegram bot",
        variant: "destructive",
      });
      return;
    }

    setLinkingTelegram(true);
    try {
      const token = useAuthStore.getState().accessToken;
      const apiBase = typeof window !== "undefined" && window.ENV?.API_URL
        ? `${window.ENV.API_URL}/api/v1`
        : "/api/v1";
      const res = await fetch(`${apiBase}/auth/telegram/link`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ code: telegramCode }),
      });

      if (!res.ok) {
        const errorBody = await res.text();
        throw new Error(errorBody || "Failed to link Telegram");
      }

      toast({
        title: "Telegram linked",
        description: "Your Telegram account has been linked successfully",
        variant: "success",
      });
      setTelegramCode("");
    } catch (err) {
      toast({
        title: "Link failed",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setLinkingTelegram(false);
    }
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>

      {/* Profile */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base">Profile</CardTitle>
          <CardDescription>Your account information</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Name</Label>
              <Input value={user?.name ?? ""} readOnly className="bg-muted/30" />
            </div>
            <div className="space-y-2">
              <Label>Email</Label>
              <Input value={user?.email ?? ""} readOnly className="bg-muted/30" />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Label>Role</Label>
            <Badge variant="outline" className="capitalize">{user?.role}</Badge>
          </div>
        </CardContent>
      </Card>

      {/* Telegram */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base">Telegram Notifications</CardTitle>
          <CardDescription>
            Link your Telegram account to receive real-time alerts
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {user?.telegram_chat_id ? (
            <div className="flex items-center gap-2">
              <Badge variant="success">Linked</Badge>
              <span className="text-sm text-muted-foreground">
                Telegram chat ID: {user.telegram_chat_id}
              </span>
            </div>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                1. Message the bot with <code className="font-mono text-primary">/start</code>{" "}
                to get a 6-character code
              </p>
              <div className="flex gap-2">
                <Input
                  placeholder="Enter 6-character code"
                  value={telegramCode}
                  onChange={(e) => setTelegramCode(e.target.value.toUpperCase())}
                  maxLength={6}
                  className="w-48 font-mono tracking-widest"
                />
                <Button
                  onClick={handleTelegramLink}
                  disabled={linkingTelegram || telegramCode.length !== 6}
                >
                  {linkingTelegram ? "Linking..." : "Link"}
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Integrations */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base">Integrations</CardTitle>
          <CardDescription>API keys and external services status</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {[
              { name: "Gemini API", env: "GEMINI_API_KEY" },
              { name: "Anthropic (Claude)", env: "ANTHROPIC_API_KEY" },
              { name: "OpenAI (GPT)", env: "OPENAI_API_KEY" },
              { name: "E2B Sandbox", env: "E2B_API_KEY" },
              { name: "Freelancer.com", env: "FREELANCER_CLIENT_ID" },
              { name: "Hunter.io", env: "HUNTER_API_KEY" },
            ].map((integration) => (
              <div
                key={integration.env}
                className="flex items-center justify-between py-2"
              >
                <div>
                  <p className="text-sm font-medium">{integration.name}</p>
                  <p className="text-xs text-muted-foreground font-mono">
                    {integration.env}
                  </p>
                </div>
                <Badge variant="outline" className="text-xs text-muted-foreground">
                  Server-side
                </Badge>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Separator />

      {/* System info */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base">System Info</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-muted-foreground">Version</p>
              <p className="font-medium font-mono">1.0.0</p>
            </div>
            <div>
              <p className="text-muted-foreground">Environment</p>
              <p className="font-medium font-mono">production</p>
            </div>
            <div>
              <p className="text-muted-foreground">Account Created</p>
              <p className="font-medium">
                {user?.created_at
                  ? new Date(user.created_at).toLocaleDateString()
                  : "N/A"}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Last Login</p>
              <p className="font-medium">
                {user?.last_login_at
                  ? new Date(user.last_login_at).toLocaleDateString()
                  : "N/A"}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
