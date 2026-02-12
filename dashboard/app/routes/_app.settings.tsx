import { useState, useEffect, useCallback } from "react";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "~/components/ui/card";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Separator } from "~/components/ui/separator";
import { Badge } from "~/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "~/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { useAuthStore } from "~/stores/auth-store";
import { toast } from "~/hooks/use-toast";
import type { CredentialsSummary, CredentialTestResult, PlatformAccount } from "~/lib/types";
import {
  fetchCredentials,
  saveAPIKeys,
  createPlatformAccount,
  deletePlatformAccount,
  testCredential,
} from "~/lib/api";

// --- Constants ---

const API_KEY_DEFINITIONS: { key: string; label: string }[] = [
  { key: "openrouter", label: "OpenRouter" },
  { key: "gemini", label: "Gemini" },
  { key: "anthropic", label: "Anthropic (Claude)" },
  { key: "openai", label: "OpenAI (GPT)" },
  { key: "e2b", label: "E2B Sandbox" },
  { key: "hunter", label: "Hunter.io" },
  { key: "apollo", label: "Apollo.io" },
  { key: "langsmith", label: "LangSmith" },
];

const PLATFORM_OPTIONS = [
  { value: "freelancer", label: "Freelancer.com" },
  { value: "upwork", label: "Upwork" },
  { value: "fl_ru", label: "FL.ru" },
  { value: "kwork", label: "Kwork" },
];

const PLATFORM_CREDENTIAL_FIELDS: Record<string, { key: string; label: string; type: string }[]> = {
  freelancer: [
    { key: "client_id", label: "Client ID", type: "text" },
    { key: "client_secret", label: "Client Secret", type: "password" },
  ],
  upwork: [
    { key: "email", label: "Email", type: "email" },
    { key: "password", label: "Password", type: "password" },
  ],
  fl_ru: [],
  kwork: [
    { key: "email", label: "Email", type: "email" },
    { key: "password", label: "Password", type: "password" },
  ],
};

// --- Helper: status badge ---

function statusBadge(status: string) {
  switch (status) {
    case "active":
      return <Badge variant="success">Active</Badge>;
    case "suspended":
    case "banned":
      return <Badge variant="destructive">{status}</Badge>;
    case "pending":
      return <Badge variant="warning">Pending</Badge>;
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}

// --- Main component ---

export default function SettingsPage() {
  const user = useAuthStore((s) => s.user);

  // Telegram state
  const [telegramCode, setTelegramCode] = useState("");
  const [linkingTelegram, setLinkingTelegram] = useState(false);

  // Credentials state
  const [credentials, setCredentials] = useState<CredentialsSummary | null>(null);
  const [loadingCredentials, setLoadingCredentials] = useState(true);

  // API keys editing
  const [editingKeys, setEditingKeys] = useState<Record<string, boolean>>({});
  const [keyValues, setKeyValues] = useState<Record<string, string>>({});
  const [savingKeys, setSavingKeys] = useState(false);

  // Platform accounts
  const [showAddAccount, setShowAddAccount] = useState(false);
  const [newAccountPlatform, setNewAccountPlatform] = useState("");
  const [newAccountUsername, setNewAccountUsername] = useState("");
  const [newAccountCreds, setNewAccountCreds] = useState<Record<string, string>>({});
  const [savingAccount, setSavingAccount] = useState(false);
  const [deletingAccountId, setDeletingAccountId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  // Credential testing
  const [testingKey, setTestingKey] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, CredentialTestResult>>({});

  // Load credentials on mount
  const loadCredentials = useCallback(async () => {
    setLoadingCredentials(true);
    try {
      const data = await fetchCredentials();
      setCredentials(data);
    } catch (err) {
      toast({
        title: "Failed to load credentials",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setLoadingCredentials(false);
    }
  }, []);

  useEffect(() => {
    loadCredentials();
  }, [loadCredentials]);

  // --- Telegram ---

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
      const apiBase =
        typeof window !== "undefined" && window.ENV?.API_URL
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

  // --- API Keys ---

  const handleToggleEditKey = (key: string) => {
    setEditingKeys((prev) => ({ ...prev, [key]: !prev[key] }));
    if (!editingKeys[key]) {
      // Entering edit mode: clear the value field
      setKeyValues((prev) => ({ ...prev, [key]: "" }));
    }
  };

  const handleSaveKeys = async () => {
    const changedKeys: Record<string, string> = {};
    for (const [key, value] of Object.entries(keyValues)) {
      if (editingKeys[key] && value.trim()) {
        changedKeys[key] = value.trim();
      }
    }

    if (Object.keys(changedKeys).length === 0) {
      toast({
        title: "No changes",
        description: "Enter at least one API key value to save",
        variant: "destructive",
      });
      return;
    }

    setSavingKeys(true);
    try {
      await saveAPIKeys(changedKeys);
      toast({
        title: "API keys saved",
        description: `Updated ${Object.keys(changedKeys).length} key(s) successfully`,
        variant: "success",
      });
      // Reset editing state and reload
      setEditingKeys({});
      setKeyValues({});
      await loadCredentials();
    } catch (err) {
      toast({
        title: "Failed to save API keys",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setSavingKeys(false);
    }
  };

  const hasKeyChanges = Object.entries(editingKeys).some(
    ([key, editing]) => editing && keyValues[key]?.trim()
  );

  // --- Credential Testing ---

  const handleTestKey = async (keyName: string) => {
    setTestingKey(keyName);
    try {
      const result = await testCredential(keyName);
      setTestResults((prev) => ({ ...prev, [keyName]: result }));
      if (result.success) {
        toast({
          title: "Key valid",
          description: result.latency_ms != null
            ? `${result.message} (${result.latency_ms}ms)`
            : result.message,
          variant: "success",
        });
      } else {
        toast({
          title: "Key invalid",
          description: result.message,
          variant: "destructive",
        });
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      setTestResults((prev) => ({
        ...prev,
        [keyName]: {
          key_name: keyName,
          success: false,
          message,
          latency_ms: null,
        },
      }));
      toast({
        title: "Test failed",
        description: message,
        variant: "destructive",
      });
    } finally {
      setTestingKey(null);
    }
  };

  // --- Platform Accounts ---

  const handleAddAccount = async () => {
    if (!newAccountPlatform) {
      toast({
        title: "Select a platform",
        description: "Please choose a platform from the dropdown",
        variant: "destructive",
      });
      return;
    }

    const fields = PLATFORM_CREDENTIAL_FIELDS[newAccountPlatform] || [];
    const missingFields = fields.filter((f) => !newAccountCreds[f.key]?.trim());
    if (missingFields.length > 0) {
      toast({
        title: "Missing credentials",
        description: `Please fill in: ${missingFields.map((f) => f.label).join(", ")}`,
        variant: "destructive",
      });
      return;
    }

    setSavingAccount(true);
    try {
      await createPlatformAccount({
        platform: newAccountPlatform,
        username: newAccountUsername.trim() || undefined,
        credentials: newAccountCreds,
      });
      toast({
        title: "Account added",
        description: `${PLATFORM_OPTIONS.find((p) => p.value === newAccountPlatform)?.label} account connected`,
        variant: "success",
      });
      // Reset form and close dialog
      setShowAddAccount(false);
      setNewAccountPlatform("");
      setNewAccountUsername("");
      setNewAccountCreds({});
      await loadCredentials();
    } catch (err) {
      toast({
        title: "Failed to add account",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setSavingAccount(false);
    }
  };

  const handleDeleteAccount = async (id: string) => {
    setDeletingAccountId(id);
    try {
      await deletePlatformAccount(id);
      toast({
        title: "Account removed",
        description: "Platform account has been deleted",
        variant: "success",
      });
      setConfirmDeleteId(null);
      await loadCredentials();
    } catch (err) {
      toast({
        title: "Failed to delete account",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setDeletingAccountId(null);
    }
  };

  const platformAccounts: PlatformAccount[] =
    credentials?.platform_accounts ?? [];

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
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
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
            <Badge variant="outline" className="capitalize">
              {user?.role}
            </Badge>
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
                1. Message the bot with{" "}
                <code className="font-mono text-primary">/start</code> to get a
                6-character code
              </p>
              <div className="flex gap-2">
                <Input
                  placeholder="Enter 6-character code"
                  value={telegramCode}
                  onChange={(e) =>
                    setTelegramCode(e.target.value.toUpperCase())
                  }
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

      {/* API Keys */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base">API Keys</CardTitle>
          <CardDescription>
            Manage API keys for LLM providers and external services
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loadingCredentials ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : (
            <div className="space-y-3">
              {API_KEY_DEFINITIONS.map(({ key, label }) => {
                const apiKeyName = `${key}_api_key`;
                const status = credentials?.api_keys?.[key] ?? credentials?.api_keys?.[apiKeyName];
                const isEditing = editingKeys[key] ?? false;
                const testResult = testResults[apiKeyName];
                const isTesting = testingKey === apiKeyName;

                return (
                  <div
                    key={key}
                    className="flex items-center justify-between gap-4 py-2"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{label}</p>
                      {isEditing ? (
                        <Input
                          type="text"
                          placeholder={`Enter ${label} key`}
                          value={keyValues[key] ?? ""}
                          onChange={(e) =>
                            setKeyValues((prev) => ({
                              ...prev,
                              [key]: e.target.value,
                            }))
                          }
                          className="mt-1.5 font-mono text-xs"
                          autoComplete="off"
                          required
                          minLength={10}
                        />
                      ) : (
                        <p className="text-xs text-muted-foreground font-mono mt-0.5">
                          {status?.masked ?? "Not configured"}
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {testResult && !isTesting && (
                        <span
                          className={`text-sm font-bold ${testResult.success ? "text-green-500" : "text-red-500"}`}
                          title={testResult.message}
                        >
                          {testResult.success ? "\u2713" : "\u2717"}
                        </span>
                      )}
                      {status?.configured ? (
                        <Badge variant="success" className="text-xs">
                          Configured
                        </Badge>
                      ) : (
                        <Badge
                          variant="outline"
                          className="text-xs text-muted-foreground"
                        >
                          Not set
                        </Badge>
                      )}
                      {status?.configured && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleTestKey(apiKeyName)}
                          disabled={isTesting || testingKey !== null}
                          className="text-xs px-2"
                        >
                          {isTesting ? "Testing..." : "Test"}
                        </Button>
                      )}
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleToggleEditKey(key)}
                      >
                        {isEditing ? "Cancel" : "Edit"}
                      </Button>
                    </div>
                  </div>
                );
              })}

              {hasKeyChanges && (
                <div className="pt-3 border-t border-border/50">
                  <Button
                    onClick={handleSaveKeys}
                    disabled={savingKeys}
                    className="w-full sm:w-auto"
                  >
                    {savingKeys ? "Saving..." : "Save API Keys"}
                  </Button>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Platform Accounts */}
      <Card className="border-border/50">
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div className="space-y-1.5">
            <CardTitle className="text-base">Platform Accounts</CardTitle>
            <CardDescription>
              Connected freelance platform accounts
            </CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowAddAccount(true)}
          >
            Add Account
          </Button>
        </CardHeader>
        <CardContent>
          {loadingCredentials ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : platformAccounts.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No platform accounts connected yet. Click &quot;Add Account&quot;
              to get started.
            </p>
          ) : (
            <div className="space-y-3">
              {platformAccounts.map((account) => (
                <div
                  key={account.id}
                  className="flex items-center justify-between gap-4 py-3 border-b border-border/30 last:border-0"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium capitalize">
                        {PLATFORM_OPTIONS.find(
                          (p) => p.value === account.platform
                        )?.label ?? account.platform}
                      </p>
                      {statusBadge(account.status)}
                    </div>
                    {account.username && (
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {account.username}
                      </p>
                    )}
                    {Object.keys(account.credentials_masked).length > 0 && (
                      <p className="text-xs text-muted-foreground font-mono mt-0.5">
                        {Object.entries(account.credentials_masked)
                          .map(([k, v]) => `${k}: ${v}`)
                          .join(" | ")}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => setConfirmDeleteId(account.id)}
                      disabled={deletingAccountId === account.id}
                    >
                      {deletingAccountId === account.id
                        ? "Deleting..."
                        : "Delete"}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Add Account Dialog */}
      <Dialog open={showAddAccount} onOpenChange={setShowAddAccount}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Platform Account</DialogTitle>
            <DialogDescription>
              Connect a new freelance platform account
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>Platform</Label>
              <Select
                value={newAccountPlatform}
                onValueChange={(val) => {
                  setNewAccountPlatform(val);
                  setNewAccountCreds({});
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select platform" />
                </SelectTrigger>
                <SelectContent>
                  {PLATFORM_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label>Username (optional)</Label>
              <Input
                type="text"
                placeholder="Your platform username"
                value={newAccountUsername}
                onChange={(e) => setNewAccountUsername(e.target.value)}
                autoComplete="off"
              />
            </div>

            {newAccountPlatform &&
              (PLATFORM_CREDENTIAL_FIELDS[newAccountPlatform]?.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No credentials required for this platform (RSS-based).
                </p>
              ) : (
                PLATFORM_CREDENTIAL_FIELDS[newAccountPlatform]?.map((field) => (
                  <div key={field.key} className="space-y-2">
                    <Label>{field.label}</Label>
                    <Input
                      type={field.type}
                      placeholder={field.label}
                      value={newAccountCreds[field.key] ?? ""}
                      onChange={(e) =>
                        setNewAccountCreds((prev) => ({
                          ...prev,
                          [field.key]: e.target.value,
                        }))
                      }
                      autoComplete="off"
                      required
                    />
                  </div>
                ))
              ))}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setShowAddAccount(false)}
              disabled={savingAccount}
            >
              Cancel
            </Button>
            <Button
              onClick={handleAddAccount}
              disabled={savingAccount || !newAccountPlatform}
            >
              {savingAccount ? "Saving..." : "Add Account"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={confirmDeleteId !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmDeleteId(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm Deletion</DialogTitle>
            <DialogDescription>
              Are you sure you want to remove this platform account? This action
              cannot be undone. All stored credentials will be permanently
              deleted.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmDeleteId(null)}
              disabled={deletingAccountId !== null}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (confirmDeleteId) handleDeleteAccount(confirmDeleteId);
              }}
              disabled={deletingAccountId !== null}
            >
              {deletingAccountId !== null ? "Deleting..." : "Delete Account"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Separator />

      {/* System Info */}
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
