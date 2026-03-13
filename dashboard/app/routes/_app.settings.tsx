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
import { cn } from "~/lib/utils";
import type { CredentialsSummary, CredentialTestResult, PlatformAccount, ScoutConfig } from "~/lib/types";
import {
  fetchCredentials,
  saveAPIKeys,
  createPlatformAccount,
  deletePlatformAccount,
  testCredential,
  fetchScoutConfig,
  saveScoutConfig,
} from "~/lib/api";

// --- Constants ---

const API_KEY_DEFINITIONS: { key: string; label: string }[] = [
  { key: "gemini", label: "Gemini" },
  { key: "anthropic", label: "Anthropic" },
  { key: "openai", label: "OpenAI" },
  { key: "openrouter", label: "OpenRouter" },
  { key: "e2b", label: "E2B" },
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

const GEO_CATEGORIES: { value: string; label: string }[] = [
  { value: "auto_repair", label: "Auto Repair" },
  { value: "dental", label: "Dental Clinics" },
  { value: "beauty", label: "Beauty" },
  { value: "restaurants", label: "Restaurants" },
  { value: "system_lab", label: "System Lab" },
  { value: "laundries", label: "Laundries" },
  { value: "pet_vet", label: "Pet / Vet" },
  { value: "flower_shops", label: "Flower Shops" },
  { value: "car_washes", label: "Car Washes" },
  { value: "med_clinics", label: "Med Clinics" },
  { value: "pharmacies", label: "Pharmacies" },
];

const DEFAULT_GEO_SELECTED = new Set(["auto_repair", "dental", "beauty", "med_clinics"]);

const PROSPECT_RULES = [
  "Only contact businesses with a published email address or contact form on their site",
  "Prioritize businesses active on social media and with consistently positive reviews (3.5+ stars)",
  "Skip chains",
];

// --- Scout Config section ---

const ALL_CATEGORIES: { value: string; label: string }[] = [
  { value: "landings", label: "Landing pages" },
  { value: "bots", label: "Telegram / Chat bots" },
  { value: "api_backend", label: "API & Backend" },
  { value: "design", label: "Web Design" },
  { value: "content", label: "Content" },
  { value: "small_fixes", label: "Small fixes" },
  { value: "mobile_apps", label: "Mobile apps" },
  { value: "ml_ai", label: "ML / AI" },
  { value: "devops", label: "DevOps" },
  { value: "consulting", label: "Consulting" },
  { value: "ecommerce", label: "E-commerce" },
  { value: "system_integration", label: "System integration" },
];

function ScoutConfigSection() {
  const [config, setConfig] = useState<ScoutConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [newRule, setNewRule] = useState("");

  useEffect(() => {
    fetchScoutConfig()
      .then(setConfig)
      .catch(() => toast({ title: "Failed to load Scout config", variant: "destructive" }))
      .finally(() => setLoading(false));
  }, []);

  const handleToggle = (category: string, column: "auto" | "suggest") => {
    if (!config) return;
    const auto = new Set(config.categories_auto);
    const suggest = new Set(config.categories_suggest);
    auto.delete(category);
    suggest.delete(category);
    if (column === "auto") auto.add(category);
    else suggest.add(category);
    setConfig({ ...config, categories_auto: [...auto], categories_suggest: [...suggest] });
  };

  const handleAddRule = () => {
    if (!config || !newRule.trim()) return;
    if (config.custom_rules.length >= 50) {
      toast({ title: "Max 50 custom rules", variant: "destructive" });
      return;
    }
    setConfig({ ...config, custom_rules: [...config.custom_rules, newRule.trim()] });
    setNewRule("");
  };

  const handleRemoveRule = (idx: number) => {
    if (!config) return;
    setConfig({ ...config, custom_rules: config.custom_rules.filter((_, i) => i !== idx) });
  };

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    try {
      await saveScoutConfig(config);
      toast({ title: "Scout configuration saved" });
    } catch (err) {
      toast({
        title: "Failed to save",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Card className="border-border/50 bg-zinc-900">
        <CardHeader>
          <CardTitle className="text-base">Scout Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Loading...</p>
        </CardContent>
      </Card>
    );
  }

  if (!config) return null;

  return (
    <Card className="border-border/50 bg-zinc-900">
      <CardHeader>
        <CardTitle className="text-base">Scout Configuration</CardTitle>
        <CardDescription>
          Configure which job categories the Scout agent handles automatically.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div>
          <div className="grid grid-cols-3 gap-2 mb-2 text-xs font-medium text-muted-foreground">
            <span>Category</span>
            <span className="text-center">AI handles</span>
            <span className="text-center">Suggest to me</span>
          </div>
          <div className="space-y-1">
            {ALL_CATEGORIES.map(({ value, label }) => {
              const isAuto = config.categories_auto.includes(value);
              const isSuggest = config.categories_suggest.includes(value);
              return (
                <div key={value} className="grid grid-cols-3 gap-2 items-center py-1.5 border-b border-border/20">
                  <span className="text-sm">{label}</span>
                  <div className="flex justify-center">
                    <input
                      type="radio"
                      name={`cat-${value}`}
                      checked={isAuto}
                      onChange={() => handleToggle(value, "auto")}
                      className="h-4 w-4 accent-orange-500"
                    />
                  </div>
                  <div className="flex justify-center">
                    <input
                      type="radio"
                      name={`cat-${value}`}
                      checked={isSuggest}
                      onChange={() => handleToggle(value, "suggest")}
                      className="h-4 w-4 accent-orange-500"
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="space-y-3">
          <Label className="text-sm font-medium">Custom Rules</Label>
          <p className="text-xs text-muted-foreground">
            Free-text instructions for the Scout LLM (max 50 rules, 500 chars each).
          </p>
          {config.custom_rules.map((rule, idx) => (
            <div key={idx} className="flex items-start gap-2">
              <span className="flex-1 text-sm bg-zinc-800 rounded px-3 py-2">{rule}</span>
              <Button variant="ghost" size="sm" onClick={() => handleRemoveRule(idx)} className="text-destructive shrink-0">
                Remove
              </Button>
            </div>
          ))}
          <div className="flex gap-2">
            <Input
              value={newRule}
              onChange={(e) => setNewRule(e.target.value)}
              placeholder="e.g. If the job mentions Figma — take it"
              maxLength={500}
              onKeyDown={(e) => e.key === "Enter" && handleAddRule()}
            />
            <Button variant="outline" onClick={handleAddRule} disabled={!newRule.trim()}>Add</Button>
          </div>
        </div>

        <div className="flex justify-end">
          <Button onClick={handleSave} disabled={saving} className="bg-orange-500 hover:bg-orange-600 text-white">
            {saving ? "Saving..." : "Save Scout Config"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
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

  // Geo categories
  const [geoSelected, setGeoSelected] = useState<Set<string>>(new Set(DEFAULT_GEO_SELECTED));

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

  useEffect(() => { loadCredentials(); }, [loadCredentials]);

  // --- Telegram ---
  const handleTelegramLink = async () => {
    if (!telegramCode.trim() || telegramCode.length !== 6) {
      toast({ title: "Invalid code", description: "Please enter the 6-character code from the Telegram bot", variant: "destructive" });
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
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ code: telegramCode }),
      });
      if (!res.ok) {
        const errorBody = await res.text();
        throw new Error(errorBody || "Failed to link Telegram");
      }
      toast({ title: "Telegram linked", description: "Your Telegram account has been linked successfully", variant: "success" });
      setTelegramCode("");
    } catch (err) {
      toast({ title: "Link failed", description: err instanceof Error ? err.message : "Unknown error", variant: "destructive" });
    } finally {
      setLinkingTelegram(false);
    }
  };

  // --- API Keys ---
  const handleToggleEditKey = (key: string) => {
    setEditingKeys((prev) => ({ ...prev, [key]: !prev[key] }));
    if (!editingKeys[key]) {
      setKeyValues((prev) => ({ ...prev, [key]: "" }));
    }
  };

  const handleSaveKeys = async () => {
    const changedKeys: Record<string, string> = {};
    for (const [key, value] of Object.entries(keyValues)) {
      if (editingKeys[key] && value.trim()) changedKeys[key] = value.trim();
    }
    if (Object.keys(changedKeys).length === 0) {
      toast({ title: "No changes", description: "Enter at least one API key value to save", variant: "destructive" });
      return;
    }
    setSavingKeys(true);
    try {
      const backendKeys: Record<string, string> = {};
      for (const [key, value] of Object.entries(changedKeys)) {
        backendKeys[`${key}_api_key`] = value;
      }
      await saveAPIKeys(backendKeys);
      toast({ title: "API keys saved", description: `Updated ${Object.keys(changedKeys).length} key(s) successfully`, variant: "success" });
      setEditingKeys({});
      setKeyValues({});
      await loadCredentials();
    } catch (err) {
      toast({ title: "Failed to save API keys", description: err instanceof Error ? err.message : "Unknown error", variant: "destructive" });
    } finally {
      setSavingKeys(false);
    }
  };

  const hasKeyChanges = Object.entries(editingKeys).some(([key, editing]) => editing && keyValues[key]?.trim());

  const handleTestKey = async (keyName: string) => {
    setTestingKey(keyName);
    try {
      const result = await testCredential(keyName);
      setTestResults((prev) => ({ ...prev, [keyName]: result }));
      if (result.success) {
        toast({ title: "Key valid", description: result.latency_ms != null ? `${result.message} (${result.latency_ms}ms)` : result.message, variant: "success" });
      } else {
        toast({ title: "Key invalid", description: result.message, variant: "destructive" });
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      setTestResults((prev) => ({ ...prev, [keyName]: { key_name: keyName, success: false, message, latency_ms: null } }));
      toast({ title: "Test failed", description: message, variant: "destructive" });
    } finally {
      setTestingKey(null);
    }
  };

  const handleAddAccount = async () => {
    if (!newAccountPlatform) {
      toast({ title: "Select a platform", variant: "destructive" });
      return;
    }
    const fields = PLATFORM_CREDENTIAL_FIELDS[newAccountPlatform] || [];
    const missingFields = fields.filter((f) => !newAccountCreds[f.key]?.trim());
    if (missingFields.length > 0) {
      toast({ title: "Missing credentials", description: `Please fill in: ${missingFields.map((f) => f.label).join(", ")}`, variant: "destructive" });
      return;
    }
    setSavingAccount(true);
    try {
      await createPlatformAccount({ platform: newAccountPlatform, username: newAccountUsername.trim() || undefined, credentials: newAccountCreds });
      toast({ title: "Account added", description: `${PLATFORM_OPTIONS.find((p) => p.value === newAccountPlatform)?.label} account connected`, variant: "success" });
      setShowAddAccount(false);
      setNewAccountPlatform("");
      setNewAccountUsername("");
      setNewAccountCreds({});
      await loadCredentials();
    } catch (err) {
      toast({ title: "Failed to add account", description: err instanceof Error ? err.message : "Unknown error", variant: "destructive" });
    } finally {
      setSavingAccount(false);
    }
  };

  const handleDeleteAccount = async (id: string) => {
    setDeletingAccountId(id);
    try {
      await deletePlatformAccount(id);
      toast({ title: "Account removed", variant: "success" });
      setConfirmDeleteId(null);
      await loadCredentials();
    } catch (err) {
      toast({ title: "Failed to delete account", description: err instanceof Error ? err.message : "Unknown error", variant: "destructive" });
    } finally {
      setDeletingAccountId(null);
    }
  };

  const platformAccounts: PlatformAccount[] = credentials?.platform_accounts ?? [];

  return (
    <div className="space-y-6 max-w-3xl">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>

      {/* Profile */}
      <Card className="border-border/50 bg-zinc-900">
        <CardHeader>
          <CardTitle className="text-base">Profile</CardTitle>
          <CardDescription>Your account information</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-4">
            <div className="h-14 w-14 rounded-full bg-orange-500/20 flex items-center justify-center text-orange-400 font-bold text-lg shrink-0">
              {user?.name ? user.name.slice(0, 2).toUpperCase() : "??"}
            </div>
            <div className="flex-1">
              <p className="text-sm font-medium">{user?.name ?? "—"}</p>
              <p className="text-xs text-muted-foreground">{user?.email ?? "—"}</p>
              <Badge variant="outline" className="capitalize mt-1 text-xs">{user?.role}</Badge>
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Name</Label>
              <Input value={user?.name ?? ""} readOnly className="bg-zinc-800/50" />
            </div>
            <div className="space-y-2">
              <Label>Email</Label>
              <Input value={user?.email ?? ""} readOnly className="bg-zinc-800/50" />
            </div>
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Avatar</Label>
            <div className="mt-1 h-16 w-24 rounded-lg border border-dashed border-border/50 flex items-center justify-center">
              <span className="text-xs text-muted-foreground">Upload</span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Telegram */}
      <Card className="border-border/50 bg-zinc-900">
        <CardHeader>
          <CardTitle className="text-base">Telegram Link</CardTitle>
          <CardDescription>Link your Telegram account for real-time notifications</CardDescription>
        </CardHeader>
        <CardContent>
          {user?.telegram_chat_id ? (
            <div className="flex items-center gap-2">
              <Badge variant="success">Linked</Badge>
              <span className="text-sm text-muted-foreground">Chat ID: {user.telegram_chat_id}</span>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Message the bot with <code className="font-mono text-orange-400">/start</code> to get a 6-character code
              </p>
              <div className="flex gap-2">
                <Input
                  placeholder="Bot Code (e.g. 478324)"
                  value={telegramCode}
                  onChange={(e) => setTelegramCode(e.target.value.toUpperCase())}
                  maxLength={6}
                  className="w-48 font-mono tracking-widest"
                />
                <Button
                  className="bg-orange-500 hover:bg-orange-600 text-white"
                  onClick={handleTelegramLink}
                  disabled={linkingTelegram || telegramCode.length !== 6}
                >
                  {linkingTelegram ? "Linking..." : "Activate"}
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* API Keys — table layout */}
      <Card className="border-border/50 bg-zinc-900">
        <CardHeader>
          <CardTitle className="text-base">API Keys</CardTitle>
          <CardDescription>Connected accounts and API keys</CardDescription>
        </CardHeader>
        <CardContent>
          {loadingCredentials ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border/30">
                    <th className="text-left text-xs text-muted-foreground font-medium pb-2 pr-4">Key Name</th>
                    <th className="text-left text-xs text-muted-foreground font-medium pb-2 pr-4">Status</th>
                    <th className="text-left text-xs text-muted-foreground font-medium pb-2 pr-4">Value</th>
                    <th className="text-left text-xs text-muted-foreground font-medium pb-2 pr-4">Active</th>
                    <th className="text-right text-xs text-muted-foreground font-medium pb-2">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {API_KEY_DEFINITIONS.map(({ key, label }) => {
                    const apiKeyName = `${key}_api_key`;
                    const status = credentials?.api_keys?.[key] ?? credentials?.api_keys?.[apiKeyName];
                    const isEditing = editingKeys[key] ?? false;
                    const testResult = testResults[apiKeyName];
                    const isTesting = testingKey === apiKeyName;
                    const isConfigured = status?.configured ?? false;

                    return (
                      <tr key={key} className="border-b border-border/20 last:border-0">
                        <td className="py-3 pr-4 font-medium">{label}</td>
                        <td className="py-3 pr-4">
                          <div className="flex items-center gap-1.5">
                            <div className={cn("h-2 w-2 rounded-full", isConfigured ? "bg-emerald-400" : "bg-zinc-600")} />
                            <span className={cn("text-xs", isConfigured ? "text-emerald-400" : "text-muted-foreground")}>
                              {isConfigured ? "Connected" : "Not set"}
                            </span>
                          </div>
                        </td>
                        <td className="py-3 pr-4">
                          {isEditing ? (
                            <Input
                              type="text"
                              placeholder={`Enter ${label} key`}
                              value={keyValues[key] ?? ""}
                              onChange={(e) => setKeyValues((prev) => ({ ...prev, [key]: e.target.value }))}
                              className="h-7 text-xs font-mono w-48"
                              autoComplete="off"
                            />
                          ) : (
                            <span className="text-xs text-muted-foreground font-mono">
                              {status?.masked ?? "***"}
                            </span>
                          )}
                        </td>
                        <td className="py-3 pr-4">
                          <div className={cn(
                            "h-4 w-8 rounded-full relative transition-colors cursor-default",
                            isConfigured ? "bg-emerald-500" : "bg-zinc-700"
                          )}>
                            <div className={cn(
                              "absolute top-0.5 h-3 w-3 rounded-full bg-white transition-transform shadow",
                              isConfigured ? "translate-x-4" : "translate-x-0.5"
                            )} />
                          </div>
                        </td>
                        <td className="py-3">
                          <div className="flex items-center gap-1 justify-end">
                            {testResult && !isTesting && (
                              <span className={cn("text-sm font-bold mr-1", testResult.success ? "text-emerald-400" : "text-red-400")}
                                title={testResult.message}>
                                {testResult.success ? "✓" : "✗"}
                              </span>
                            )}
                            {isConfigured && (
                              <Button variant="ghost" size="sm" className="h-7 text-xs px-2"
                                onClick={() => handleTestKey(apiKeyName)}
                                disabled={isTesting || testingKey !== null}>
                                {isTesting ? "..." : "Test"}
                              </Button>
                            )}
                            <Button variant="ghost" size="sm" className="h-7 text-xs px-2"
                              onClick={() => handleToggleEditKey(key)}>
                              {isEditing ? "Cancel" : "Edit"}
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {hasKeyChanges && (
                <div className="pt-3 border-t border-border/50 mt-3">
                  <Button onClick={handleSaveKeys} disabled={savingKeys} className="bg-orange-500 hover:bg-orange-600 text-white">
                    {savingKeys ? "Saving..." : "Save API Keys"}
                  </Button>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Platform Accounts — table layout */}
      <Card className="border-border/50 bg-zinc-900">
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div className="space-y-1.5">
            <CardTitle className="text-base">Platform Accounts</CardTitle>
            <CardDescription>Connected freelance platform accounts</CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => setShowAddAccount(true)}>
            Add Account
          </Button>
        </CardHeader>
        <CardContent>
          {loadingCredentials ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : platformAccounts.length === 0 ? (
            <p className="text-sm text-muted-foreground">No platform accounts connected yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border/30">
                    <th className="text-left text-xs text-muted-foreground font-medium pb-2 pr-4">Platform</th>
                    <th className="text-left text-xs text-muted-foreground font-medium pb-2 pr-4">Username</th>
                    <th className="text-left text-xs text-muted-foreground font-medium pb-2 pr-4">Status</th>
                    <th className="text-left text-xs text-muted-foreground font-medium pb-2 pr-4">Joined</th>
                    <th className="text-right text-xs text-muted-foreground font-medium pb-2">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {platformAccounts.map((account) => (
                    <tr key={account.id} className="border-b border-border/20 last:border-0">
                      <td className="py-3 pr-4 font-medium capitalize">
                        {PLATFORM_OPTIONS.find((p) => p.value === account.platform)?.label ?? account.platform}
                      </td>
                      <td className="py-3 pr-4 text-muted-foreground text-xs">
                        {account.username || "—"}
                      </td>
                      <td className="py-3 pr-4">
                        <div className="flex items-center gap-1.5">
                          <div className={cn(
                            "h-2 w-2 rounded-full",
                            account.status === "active" ? "bg-emerald-400" :
                            account.status === "suspended" || account.status === "banned" ? "bg-red-400" :
                            "bg-amber-400"
                          )} />
                          <span className="text-xs capitalize">{account.status}</span>
                        </div>
                      </td>
                      <td className="py-3 pr-4 text-xs text-muted-foreground">—</td>
                      <td className="py-3">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs text-destructive hover:text-destructive"
                          onClick={() => setConfirmDeleteId(account.id)}
                          disabled={deletingAccountId === account.id}
                        >
                          {deletingAccountId === account.id ? "Deleting..." : "Delete"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Scout Categories / Geo Targeting */}
      <Card className="border-border/50 bg-zinc-900">
        <CardHeader>
          <CardTitle className="text-base">Scout Categories</CardTitle>
          <CardDescription>Analyze data or list services to match</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {GEO_CATEGORIES.map(({ value, label }) => {
              const checked = geoSelected.has(value);
              return (
                <label key={value} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      const next = new Set(geoSelected);
                      if (checked) next.delete(value);
                      else next.add(value);
                      setGeoSelected(next);
                    }}
                    className="h-4 w-4 rounded accent-orange-500"
                  />
                  <span className="text-sm">{label}</span>
                </label>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Prospect Rules */}
      <Card className="border-border/50 bg-zinc-900">
        <CardHeader>
          <CardTitle className="text-base">Prospect Rules</CardTitle>
          <CardDescription>Who do we email, and specs</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2">
            {PROSPECT_RULES.map((rule, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-muted-foreground">
                <span className="text-orange-400 mt-0.5 shrink-0">•</span>
                {rule}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      {/* Sending Info */}
      <Card className="border-border/50 bg-zinc-900">
        <CardHeader>
          <CardTitle className="text-base">Sending Info</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Sender Name</Label>
              <Input placeholder="Your Name" className="bg-zinc-800/50" />
            </div>
            <div className="space-y-2">
              <Label>From Email</Label>
              <Input placeholder="you@domain.com" type="email" className="bg-zinc-800/50" />
            </div>
          </div>
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <span>Sending Schedule:</span>
            <span className="font-medium text-foreground">Apr 22, 2026</span>
            <span className="text-orange-400">— 2M remaining</span>
          </div>
        </CardContent>
      </Card>

      <Separator />

      {/* Scout Config (full, with categories) */}
      <ScoutConfigSection />

      <Separator />

      {/* System Info */}
      <Card className="border-border/50 bg-zinc-900">
        <CardHeader>
          <CardTitle className="text-base">System Info</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-muted-foreground">Version</p>
              <p className="font-medium font-mono">
                {(typeof window !== "undefined" && (window as any).ENV?.APP_VERSION) || "4.2.0"}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Environment</p>
              <p className="font-medium font-mono">
                {(typeof window !== "undefined" && (window as any).ENV?.NODE_ENV) || "production"}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Account Created</p>
              <p className="font-medium">
                {user?.created_at ? new Date(user.created_at).toLocaleDateString() : "N/A"}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Last Login</p>
              <p className="font-medium">
                {user?.last_login_at ? new Date(user.last_login_at).toLocaleDateString() : "N/A"}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Add Account Dialog */}
      <Dialog open={showAddAccount} onOpenChange={setShowAddAccount}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Platform Account</DialogTitle>
            <DialogDescription>Connect a new freelance platform account</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>Platform</Label>
              <Select value={newAccountPlatform} onValueChange={(val) => { setNewAccountPlatform(val); setNewAccountCreds({}); }}>
                <SelectTrigger><SelectValue placeholder="Select platform" /></SelectTrigger>
                <SelectContent>
                  {PLATFORM_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Username (optional)</Label>
              <Input type="text" placeholder="Your platform username" value={newAccountUsername} onChange={(e) => setNewAccountUsername(e.target.value)} autoComplete="off" />
            </div>
            {newAccountPlatform && (PLATFORM_CREDENTIAL_FIELDS[newAccountPlatform]?.length === 0 ? (
              <p className="text-sm text-muted-foreground">No credentials required (RSS-based).</p>
            ) : (
              PLATFORM_CREDENTIAL_FIELDS[newAccountPlatform]?.map((field) => (
                <div key={field.key} className="space-y-2">
                  <Label>{field.label}</Label>
                  <Input type={field.type} placeholder={field.label} value={newAccountCreds[field.key] ?? ""} onChange={(e) => setNewAccountCreds((prev) => ({ ...prev, [field.key]: e.target.value }))} autoComplete="off" required />
                </div>
              ))
            ))}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowAddAccount(false)} disabled={savingAccount}>Cancel</Button>
            <Button onClick={handleAddAccount} disabled={savingAccount || !newAccountPlatform}>
              {savingAccount ? "Saving..." : "Add Account"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={confirmDeleteId !== null} onOpenChange={(open) => { if (!open) setConfirmDeleteId(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm Deletion</DialogTitle>
            <DialogDescription>Are you sure you want to remove this platform account? All stored credentials will be permanently deleted.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDeleteId(null)} disabled={deletingAccountId !== null}>Cancel</Button>
            <Button variant="destructive" onClick={() => { if (confirmDeleteId) handleDeleteAccount(confirmDeleteId); }} disabled={deletingAccountId !== null}>
              {deletingAccountId !== null ? "Deleting..." : "Delete Account"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
