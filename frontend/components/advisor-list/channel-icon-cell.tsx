"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import {
  ExternalLink,
  Linkedin,
  Mail,
  Phone,
  type LucideIcon,
} from "lucide-react";

import { FindPhoneButton } from "@/components/master-list/detail/find-phone-button";
import type { EmailHit, ExecutiveContactItem, PhoneHit } from "@/lib/types";

// Three fixed icon slots per row (LinkedIn / Email / Phone), always rendered
// — missing channels show muted/disabled so the row layout stays uniform and
// "no LinkedIn" is visible at a glance. Clicking an enabled icon opens a
// portaled popover with the details. The portal is required because the
// enriched-contacts table is wrapped in overflow-hidden rounded-2xl, which
// would clip a CSS-positioned popover the same way it clipped UnknownCell's
// original sibling tooltip.

type Channel = "linkedin" | "mail" | "phone";

const POPOVER_GAP = 8;
const VIEWPORT_PADDING = 8;
const POPOVER_MAX_WIDTH = 320;

interface PopoverCoords {
  top: number;
  left: number;
  placement: "top" | "bottom";
}

// Minimal structural shape the cell needs — satisfied by both
// AdvisorContactItem (/advisor-list) and ExecutiveContactItem (/master-list),
// so the same cell drives the Channels column on both detail pages.
type ChannelContact = {
  id: number;
  email: string | null;
  phone?: string | null;
  emails?: EmailHit[] | null;
  phones?: PhoneHit[] | null;
  linkedin_url: string | null;
};

export function ChannelIconCell({
  contact,
  entityKind = "broker-dealer",
  entityId,
  onContactUpdated,
  forceActiveLook = false,
}: {
  contact: ChannelContact | null | undefined;
  // Find-phone is opt-in: only the broker-dealer page passes these, so the
  // advisor page keeps the read-only icon behavior (no find-phone affordance).
  entityKind?: "broker-dealer" | "investor";
  entityId?: number;
  onContactUpdated?: (updated: ExecutiveContactItem) => void;
  // Render every icon in the accent color even when the channel has no data
  // (the icon stays inert — no popover — but doesn't grey out). Used on the
  // broker-dealer People section so the row reads as "active" for demos.
  forceActiveLook?: boolean;
}) {
  const [openChannel, setOpenChannel] = useState<Channel | null>(null);
  const linkedinRef = useRef<HTMLButtonElement>(null);
  const mailRef = useRef<HTMLButtonElement>(null);
  const phoneRef = useRef<HTMLButtonElement>(null);

  // Defensive fallback: prefer the JSONB arrays, but lift the legacy scalar
  // email/phone onto a one-element array shape so rows from older writes
  // (pre-array merger) still show details on click.
  const emails = useMemo<EmailHit[]>(() => {
    if (contact?.emails && contact.emails.length > 0) return contact.emails;
    if (contact?.email) {
      return [
        { value: contact.email, type: "work", confidence: null, source: "" },
      ];
    }
    return [];
  }, [contact?.emails, contact?.email]);

  const phones = useMemo<PhoneHit[]>(() => {
    if (contact?.phones && contact.phones.length > 0) return contact.phones;
    if (contact?.phone) {
      return [
        { value: contact.phone, type: "work", confidence: null, source: "" },
      ];
    }
    return [];
  }, [contact?.phones, contact?.phone]);

  const linkedinUrl = contact?.linkedin_url ?? null;
  const hasLinkedIn = !!linkedinUrl;
  const hasEmail = emails.length > 0;
  const hasPhone = phones.length > 0;

  // Show a "Find phone" affordance inside the phone popover when the contact
  // has an email but no phone yet (mirrors the old ContactRow behavior). Built
  // as one object so the truthiness checks narrow the optional props for TS.
  const findPhone =
    onContactUpdated && entityId && contact && !hasPhone && contact.email
      ? {
          entityKind,
          entityId,
          contactId: contact.id,
          onSuccess: onContactUpdated,
        }
      : null;
  const canFindPhone = findPhone !== null;

  const toggle = useCallback((channel: Channel) => {
    setOpenChannel((current) => (current === channel ? null : channel));
  }, []);

  const close = useCallback(() => setOpenChannel(null), []);

  const activeRef =
    openChannel === "linkedin"
      ? linkedinRef
      : openChannel === "mail"
        ? mailRef
        : openChannel === "phone"
          ? phoneRef
          : null;

  return (
    <div className="inline-flex items-center gap-1.5">
      <IconButton
        icon={Linkedin}
        present={hasLinkedIn}
        forceActiveLook={forceActiveLook}
        label={
          hasLinkedIn
            ? "Show LinkedIn profile"
            : "No LinkedIn URL on file"
        }
        isActive={openChannel === "linkedin"}
        onClick={() => toggle("linkedin")}
        buttonRef={linkedinRef}
      />
      <IconButton
        icon={Mail}
        present={hasEmail}
        forceActiveLook={forceActiveLook}
        label={hasEmail ? "Show email addresses" : "No email on file"}
        isActive={openChannel === "mail"}
        onClick={() => toggle("mail")}
        buttonRef={mailRef}
      />
      <IconButton
        icon={Phone}
        present={hasPhone || canFindPhone}
        forceActiveLook={forceActiveLook}
        label={
          hasPhone
            ? "Show phone numbers"
            : canFindPhone
              ? "Find phone number"
              : "No phone on file"
        }
        isActive={openChannel === "phone"}
        onClick={() => toggle("phone")}
        buttonRef={phoneRef}
      />
      {openChannel && activeRef ? (
        <ChannelPopover anchorRef={activeRef} onClose={close}>
          {openChannel === "linkedin" && linkedinUrl ? (
            <LinkedInBody url={linkedinUrl} />
          ) : null}
          {openChannel === "mail" ? <EmailBody emails={emails} /> : null}
          {openChannel === "phone" ? (
            <PhoneBody phones={phones} findPhone={findPhone} />
          ) : null}
        </ChannelPopover>
      ) : null}
    </div>
  );
}

interface IconButtonProps {
  icon: LucideIcon;
  present: boolean;
  label: string;
  isActive: boolean;
  onClick: () => void;
  buttonRef: React.RefObject<HTMLButtonElement>;
  forceActiveLook?: boolean;
}

function IconButton({
  icon: Icon,
  present,
  label,
  isActive,
  onClick,
  buttonRef,
  forceActiveLook = false,
}: IconButtonProps) {
  // Present: themed accent + hover background, full opacity.
  // Absent: muted + opacity 40 + cursor-not-allowed, click is a no-op.
  // forceActiveLook keeps the accent color when absent (still inert — the
  // button stays disabled so there's no empty popover) for a fuller-looking row.
  const toneClass = present
    ? "text-[var(--accent,#6366f1)] hover:bg-[var(--surface-2,#f1f6fd)] cursor-pointer"
    : forceActiveLook
      ? "text-[var(--accent,#6366f1)] opacity-90 cursor-default"
      : "text-[var(--text-muted,#94a3b8)] opacity-40 cursor-not-allowed";
  const activeClass = isActive
    ? "bg-[var(--surface-2,#f1f6fd)] ring-1 ring-[var(--accent,#6366f1)]"
    : "";

  return (
    <button
      ref={buttonRef}
      type="button"
      aria-label={label}
      aria-expanded={isActive}
      title={label}
      disabled={!present}
      onClick={present ? onClick : undefined}
      className={`inline-flex h-7 w-7 items-center justify-center rounded-md outline-none transition focus-visible:ring-2 focus-visible:ring-[var(--accent,#6366f1)] ${toneClass} ${activeClass}`}
    >
      <Icon className="h-4 w-4" strokeWidth={2} aria-hidden />
    </button>
  );
}

interface ChannelPopoverProps {
  anchorRef: React.RefObject<HTMLButtonElement>;
  onClose: () => void;
  children: ReactNode;
}

function ChannelPopover({ anchorRef, onClose, children }: ChannelPopoverProps) {
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const [coords, setCoords] = useState<PopoverCoords | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const updatePosition = useCallback(() => {
    const button = anchorRef.current;
    if (!button) return;
    const buttonRect = button.getBoundingClientRect();
    const popoverRect = popoverRef.current?.getBoundingClientRect();
    const popoverHeight = popoverRect?.height ?? 120;
    const popoverWidth = Math.min(
      popoverRect?.width ?? POPOVER_MAX_WIDTH,
      POPOVER_MAX_WIDTH,
    );

    const spaceAbove = buttonRect.top;
    const spaceBelow = window.innerHeight - buttonRect.bottom;
    // Prefer below the icon (popover floats over the row below); flip above
    // when there's clearly more room there.
    const placement: "top" | "bottom" =
      spaceBelow >= popoverHeight + POPOVER_GAP + VIEWPORT_PADDING ||
      spaceBelow >= spaceAbove
        ? "bottom"
        : "top";

    const top =
      placement === "bottom"
        ? buttonRect.bottom + POPOVER_GAP
        : buttonRect.top - popoverHeight - POPOVER_GAP;

    const idealLeft = buttonRect.left + buttonRect.width / 2 - popoverWidth / 2;
    const left = Math.max(
      VIEWPORT_PADDING,
      Math.min(idealLeft, window.innerWidth - popoverWidth - VIEWPORT_PADDING),
    );

    setCoords({ top, left, placement });
  }, [anchorRef]);

  useLayoutEffect(() => {
    updatePosition();
  }, [updatePosition]);

  useEffect(() => {
    const handler = () => updatePosition();
    window.addEventListener("scroll", handler, true);
    window.addEventListener("resize", handler);
    return () => {
      window.removeEventListener("scroll", handler, true);
      window.removeEventListener("resize", handler);
    };
  }, [updatePosition]);

  useEffect(() => {
    const onMouseDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (popoverRef.current?.contains(target)) return;
      if (anchorRef.current?.contains(target)) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [anchorRef, onClose]);

  if (!mounted) return null;

  return createPortal(
    <div
      ref={popoverRef}
      role="dialog"
      style={{
        position: "fixed",
        top: coords?.top ?? -9999,
        left: coords?.left ?? -9999,
        maxWidth: POPOVER_MAX_WIDTH,
        zIndex: 9999,
        visibility: coords ? "visible" : "hidden",
      }}
      className="pointer-events-auto w-max rounded-lg border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-left text-[12px] leading-5 text-[var(--text,#0f172a)] shadow-[0_10px_28px_rgba(15,23,42,0.18)]"
    >
      {children}
    </div>,
    document.body,
  );
}

function LinkedInBody({ url }: { url: string }) {
  // The full URL can be long; show a truncated form with a clear "open" CTA
  // that does the actual navigation. The displayed text stays selectable.
  return (
    <div className="flex flex-col gap-2 min-w-[220px]">
      <span className="text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        LinkedIn
      </span>
      <span className="block break-all text-[12px] text-[var(--text-dim,#475569)]">
        {url}
      </span>
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 self-start rounded-md bg-[var(--accent,#6366f1)] px-2.5 py-1 text-[11px] font-medium text-white transition hover:opacity-90"
      >
        <ExternalLink className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
        Open profile
      </a>
    </div>
  );
}

function EmailBody({ emails }: { emails: EmailHit[] }) {
  return (
    <div className="flex flex-col gap-1.5 min-w-[220px]">
      <span className="text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        Email
      </span>
      <ul className="flex flex-col gap-1">
        {emails.map((email, i) => (
          <li
            key={`email-${i}`}
            className="flex items-center gap-2 break-all"
          >
            <a
              href={`mailto:${email.value}`}
              className="text-[12px] text-[var(--accent,#6366f1)] transition hover:underline"
            >
              {email.value}
            </a>
            {email.type === "personal" ? (
              <span className="rounded-full bg-[var(--surface-2,#f1f6fd)] px-1.5 py-0.5 text-[10px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                personal
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function PhoneBody({
  phones,
  findPhone,
}: {
  phones: PhoneHit[];
  findPhone?: {
    entityKind: "broker-dealer" | "investor";
    entityId: number;
    contactId: number;
    onSuccess: (updated: ExecutiveContactItem) => void;
  } | null;
}) {
  return (
    <div className="flex flex-col gap-1.5 min-w-[180px]">
      <span className="text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        Phone
      </span>
      {phones.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {phones.map((phone, i) => (
            <li key={`phone-${i}`} className="flex items-center gap-2">
              <a
                href={`tel:${phone.value}`}
                className="text-[12px] text-[var(--text-dim,#475569)] transition hover:underline"
              >
                {phone.value}
              </a>
              {phone.type !== "work" ? (
                <span className="rounded-full bg-[var(--surface-2,#f1f6fd)] px-1.5 py-0.5 text-[10px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                  {phone.type}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : findPhone ? (
        <div className="flex flex-col items-start gap-2">
          <span className="text-[12px] italic text-[var(--text-muted,#94a3b8)]">
            Mobile number not publicly available
          </span>
          <FindPhoneButton
            entityKind={findPhone.entityKind}
            entityId={findPhone.entityId}
            contactId={findPhone.contactId}
            onSuccess={findPhone.onSuccess}
          />
        </div>
      ) : null}
    </div>
  );
}
