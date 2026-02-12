import { useState } from "react";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Textarea } from "~/components/ui/textarea";
import type { CreateCampaignPayload } from "~/lib/types";

interface CampaignEditorProps {
  initial?: Partial<CreateCampaignPayload>;
  onSave: (data: CreateCampaignPayload) => Promise<void>;
  onCancel: () => void;
  saving?: boolean;
}

const TEMPLATE_VARS = ["{{name}}", "{{city}}", "{{category}}", "{{website}}"];

export function CampaignEditor({ initial, onSave, onCancel, saving }: CampaignEditorProps) {
  const [name, setName] = useState(initial?.name ?? "");
  const [subject, setSubject] = useState(initial?.subject ?? "");
  const [body, setBody] = useState(initial?.body ?? "");
  const [cityFilter, setCityFilter] = useState(initial?.city_filter ?? "");
  const [categoryFilter, setCategoryFilter] = useState(initial?.category_filter ?? "");

  const handleSubmit = async () => {
    await onSave({
      name: name.trim(),
      subject: subject.trim(),
      body: body.trim(),
      city_filter: cityFilter.trim() || undefined,
      category_filter: categoryFilter.trim() || undefined,
    });
  };

  const isValid = name.trim() && subject.trim() && body.trim();

  // Render preview with highlighted variables
  const renderPreview = (text: string) => {
    const parts = text.split(/({{[^}]+}})/g);
    return parts.map((part, i) =>
      part.match(/^{{[^}]+}}$/) ? (
        <span key={i} className="bg-primary/20 text-primary font-medium px-0.5 rounded">
          {part}
        </span>
      ) : (
        <span key={i}>{part}</span>
      )
    );
  };

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      {/* Editor */}
      <div className="space-y-4">
        <div>
          <Label htmlFor="campaign-name">Campaign Name</Label>
          <Input
            id="campaign-name"
            placeholder="e.g. Berlin Restaurants Q1"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1.5"
          />
        </div>

        <div>
          <Label htmlFor="campaign-subject">Email Subject</Label>
          <Input
            id="campaign-subject"
            placeholder="e.g. Website services for {{name}}"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            className="mt-1.5"
          />
        </div>

        <div>
          <Label htmlFor="campaign-body">Email Body</Label>
          <Textarea
            id="campaign-body"
            placeholder="Dear {{name}},&#10;&#10;I noticed your business in {{city}}..."
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={10}
            className="mt-1.5 font-mono text-sm"
          />
          <div className="flex flex-wrap gap-1.5 mt-2">
            {TEMPLATE_VARS.map((v) => (
              <button
                key={v}
                type="button"
                className="text-xs px-2 py-0.5 rounded border border-border/50 text-muted-foreground hover:bg-accent transition-colors"
                onClick={() => setBody((prev) => prev + v)}
              >
                {v}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label htmlFor="campaign-city">City Filter</Label>
            <Input
              id="campaign-city"
              placeholder="e.g. Berlin"
              value={cityFilter}
              onChange={(e) => setCityFilter(e.target.value)}
              className="mt-1.5"
            />
          </div>
          <div>
            <Label htmlFor="campaign-category">Category Filter</Label>
            <Input
              id="campaign-category"
              placeholder="e.g. restaurant"
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="mt-1.5"
            />
          </div>
        </div>

        <div className="flex items-center gap-2 pt-2">
          <Button onClick={handleSubmit} disabled={!isValid || saving}>
            {saving ? "Saving..." : initial ? "Update Campaign" : "Create Campaign"}
          </Button>
          <Button variant="outline" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
        </div>
      </div>

      {/* Preview */}
      <Card className="border-border/50">
        <CardHeader className="pb-3">
          <CardTitle className="text-base font-medium">Email Preview</CardTitle>
        </CardHeader>
        <CardContent>
          {subject ? (
            <div className="space-y-3">
              <div>
                <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Subject</p>
                <p className="text-sm font-medium">{renderPreview(subject)}</p>
              </div>
              <div className="h-px bg-border/50" />
              <div>
                <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Body</p>
                <div className="text-sm whitespace-pre-wrap leading-relaxed">
                  {renderPreview(body || "(empty)")}
                </div>
              </div>
              {(cityFilter || categoryFilter) && (
                <>
                  <div className="h-px bg-border/50" />
                  <div className="flex gap-4 text-xs text-muted-foreground">
                    {cityFilter && <span>City: {cityFilter}</span>}
                    {categoryFilter && <span>Category: {categoryFilter}</span>}
                  </div>
                </>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              Fill in the form to see a preview
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
