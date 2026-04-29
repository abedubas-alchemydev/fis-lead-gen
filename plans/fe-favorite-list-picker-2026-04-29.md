# FE Favorite List-Picker — #17 phase 3

**Branch:** `feature/fe-favorite-list-picker`
**Pairs with:** cli03 BE PR `feature/be-firm-favorite-lists-and-drop-safety-net`

## Goal

Let users add/remove a firm to/from any of their favorite lists from
the master-list rows and from the firm-detail header — not just from
inside `/my-favorites`. Phase 1 shipped a read-only multi-list view;
phase 2 made the lists writable; this phase wires the writable surface
to the rest of the app where firms are encountered.

## API contract (cli03 ships)

```
GET /api/v1/broker-dealers/{firm_id}/favorite-lists
  → FavoriteList[] with extra `is_member: boolean`

POST   /api/v1/favorite-lists/{list_id}/items   { broker_dealer_id }
DELETE /api/v1/favorite-lists/{list_id}/items/{broker_dealer_id}
```

POST/DELETE already exist from phase 2 — no new endpoints there.

## FE pieces

### 1. Types — `frontend/types/favorite-list.ts`

Extend with:

```ts
export interface FavoriteListWithMembership extends FavoriteList {
  is_member: boolean;
}
```

### 2. API methods — `frontend/lib/api.ts`

```ts
getListsForFirm(firmId: number): Promise<FavoriteListWithMembership[]>
addFirmToList(listId: number, firmId: number): Promise<void>
removeFirmFromList(listId: number, firmId: number): Promise<void>
```

(IDs are integers in this codebase — `FavoriteList.id: number`.
The phase-2 spec sketch used `string` for list ids; we follow the
existing schema.)

### 3. Shared component — `frontend/components/list-picker/list-picker.tsx`

Props:

```ts
interface ListPickerProps {
  firmId: number;
  variant: "row" | "detail";
  initialDefaultMember?: boolean; // optional: seed heart-fill for variant="detail"
}
```

Behaviour:

- Renders a trigger button (small icon for `variant="row"`, large heart
  for `variant="detail"`).
- On open, fetches `getListsForFirm(firmId)` once per session.
- Renders a checkbox list of all the user's lists.
- Default list row is rendered first and visually distinguished — that
  row is the "default-list quick-toggle" the spec calls for: a single
  click on the default checkbox preserves the one-click favorite UX.
- Toggling a checkbox calls `addFirmToList` / `removeFirmFromList`
  optimistically; on failure, revert the row's `is_member` and surface
  a toast via `useToast()`.
- Outside-click closes the popover (mousedown listener — same pattern
  as `multi-select-filter.tsx`).
- For `variant="detail"`, the heart-icon trigger reflects the default
  list's `is_member` so the existing "filled heart = favorited" cue
  carries through. While the picker hasn't loaded yet, fall back to
  `initialDefaultMember` so the heart isn't misleading on first paint.

### 4. Master-list row mount

`master-list-workspace-client.tsx` — inside the firm-name `<td>` near
line 1052. Add a small icon-button trigger (`<ListPicker variant="row"
firmId={item.id} />`) anchored to the cell. Stop propagation on the
trigger's click so the row's link to `/master-list/{id}` doesn't fire.

The `BrokerDealerListItem` shape doesn't currently carry favorited
state, so `variant="row"` does not pre-fill any visual state — it
fetches lazily on first open.

### 5. Firm-detail mount

`broker-dealer-detail-client.tsx` line 477 — replace
`<FavoriteButton bdId={bd.id} initialFavorited={profile.is_favorited} />`
with `<ListPicker variant="detail" firmId={bd.id}
initialDefaultMember={profile.is_favorited} />`.

The existing `is_favorited` field in `BrokerDealerProfileResponse`
reflects default-list membership for the legacy single-favorite UX,
so it's the correct seed for the heart's pre-load fill state.

## Edge cases

- **User has only the default list.** Picker shows ONE row with a
  checkbox; UX is the same as the old heart with extra ceremony.
- **Empty list state.** Shouldn't happen — a default list always
  exists per the BE contract — but render "No lists yet" + a link to
  `/my-favorites` if the array comes back empty.
- **Detail page heart fill before fetch.** Use `initialDefaultMember`
  as the seed, then sync to the fetched default-list `is_member` on
  open.
- **Network error on toggle.** Optimistic flip reverted; toast.error.
- **Race between open and close.** AbortController on `getListsForFirm`
  — close discards in-flight result.

## Out of scope

- Creating/renaming/deleting lists from the picker. Users still go to
  `/my-favorites` for that. (Stays in cli04's lane regardless.)
- Bulk multi-firm operations.
- Server-side state for "which lists is firm X in" cached in the row
  payload — kept lazy for now.

## Test plan

- [ ] Master-list row: trigger appears, doesn't navigate, opens picker
- [ ] Picker fetches once and reuses on re-open
- [ ] Toggling default list adds/removes correctly
- [ ] Toggling a non-default list adds/removes correctly
- [ ] Two-list toggle in succession both succeed (no batched stale state)
- [ ] Server error reverts the row + shows toast
- [ ] Outside click closes picker
- [ ] Detail page heart fills correctly from `is_favorited` pre-fetch
- [ ] Detail page heart re-syncs to default-list `is_member` post-fetch
- [ ] `npm run lint` clean
- [ ] `npm run build` clean
