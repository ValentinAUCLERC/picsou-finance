import { useEffect, useMemo, useState, type ReactNode } from "react"
import { CartesianGrid, Legend, Line, LineChart, XAxis, YAxis } from "recharts"
import { toast } from "sonner"
import {
  BarChart3,
  CalendarClock,
  Loader2,
  RefreshCw,
  Target,
  TrendingUp,
  WalletCards,
} from "lucide-react"
import { useAccounts } from "@/features/accounts/hooks"
import {
  useAnalyticsPerformance,
  useBackfill,
  useBackfillPlan,
  useFire,
  useFireSettings,
  usePortfolioLots,
  useRebalance,
  useSetRebalanceTargets,
  useUpdateFireSettings,
} from "@/features/analytics/hooks"
import { PageHeader } from "@/components/shared/PageHeader"
import { CurrencyDisplay } from "@/components/shared/CurrencyDisplay"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import { formatCurrency, formatDate } from "@/lib/utils"
import type { Account, FireSettings, RebalanceLine } from "@/types/api"

const INVESTMENT_TYPES = new Set([
  "PEA",
  "COMPTE_TITRES",
  "CRYPTO",
  "EMPLOYEE_SAVINGS",
])
const chartConfig = {
  twrPercent: { label: "Portefeuille", color: "var(--chart-1)" },
  benchmarkPercent: { label: "Indice", color: "var(--chart-4)" },
  total: { label: "Valeur", color: "var(--chart-2)" },
} satisfies ChartConfig

const iso = (date: Date) => date.toISOString().slice(0, 10)
const oneYearAgo = () =>
  iso(new Date(new Date().setFullYear(new Date().getFullYear() - 1)))
const rate = (value: number | null | undefined) =>
  value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)} %`
const number = (value: number | null | undefined, decimals = 4) =>
  value == null
    ? "—"
    : new Intl.NumberFormat(undefined, {
        maximumFractionDigits: decimals,
      }).format(value)

export function AnalyticsPage() {
  const { data: accounts = [] } = useAccounts()
  const [selected, setSelected] = useState<number[]>([])
  const [from, setFrom] = useState(oneYearAgo)
  const [to, setTo] = useState(() => iso(new Date()))
  const [benchmark, setBenchmark] = useState("^FCHI")

  useEffect(() => {
    if (selected.length === 0 && accounts.length > 0) {
      setSelected(
        accounts
          .filter((account) => INVESTMENT_TYPES.has(account.type))
          .map((account) => account.id)
      )
    }
  }, [accounts, selected.length])

  const sortedIds = useMemo(
    () => [...selected].sort((a, b) => a - b),
    [selected]
  )
  const performance = useAnalyticsPerformance(sortedIds, from, to, benchmark)
  const fire = useFire(sortedIds)
  const lots = usePortfolioLots(sortedIds)
  const rebalance = useRebalance(sortedIds)
  const backfillPlan = useBackfillPlan(sortedIds)

  const toggleAccount = (account: Account) =>
    setSelected((current) =>
      current.includes(account.id)
        ? current.filter((id) => id !== account.id)
        : [...current, account.id]
    )

  return (
    <div className="space-y-6">
      <PageHeader
        surtitle="Portfolio analytics"
        title="Analyse de portefeuille"
        actions={
          <AccountPicker
            accounts={accounts}
            selected={selected}
            onToggle={toggleAccount}
          />
        }
      />

      {selected.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-muted-foreground">
            Sélectionne au moins un compte pour calculer les analyses.
          </CardContent>
        </Card>
      ) : (
        <Tabs defaultValue="performance">
          <TabsList className="w-full justify-start overflow-x-auto">
            <TabsTrigger value="performance">
              <BarChart3 />
              Performance
            </TabsTrigger>
            <TabsTrigger value="fire">
              <Target />
              FIRE
            </TabsTrigger>
            <TabsTrigger value="lots">
              <WalletCards />
              Lignes
            </TabsTrigger>
            <TabsTrigger value="rebalance">
              <RefreshCw />
              Rééquilibrage
            </TabsTrigger>
            <TabsTrigger value="backfill">
              <CalendarClock />
              Backfill
            </TabsTrigger>
          </TabsList>
          <TabsContent value="performance" className="pt-4">
            <PerformancePanel
              {...{
                performance,
                from,
                to,
                benchmark,
                setFrom,
                setTo,
                setBenchmark,
              }}
            />
          </TabsContent>
          <TabsContent value="fire" className="pt-4">
            <FirePanel projection={fire.data} />
          </TabsContent>
          <TabsContent value="lots" className="pt-4">
            <LotsPanel lots={lots.data} loading={lots.isLoading} />
          </TabsContent>
          <TabsContent value="rebalance" className="pt-4">
            <RebalancePanel
              lines={rebalance.data?.lines ?? []}
              total={rebalance.data?.totalValue ?? 0}
              loading={rebalance.isLoading}
            />
          </TabsContent>
          <TabsContent value="backfill" className="pt-4">
            <BackfillPanel
              accountIds={sortedIds}
              plan={backfillPlan.data}
              loading={backfillPlan.isLoading}
            />
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}

function AccountPicker({
  accounts,
  selected,
  onToggle,
}: {
  accounts: Account[]
  selected: number[]
  onToggle: (account: Account) => void
}) {
  return (
    <div className="flex max-w-xl flex-wrap gap-x-4 gap-y-2 rounded-xl border bg-card px-3 py-2 text-sm">
      {accounts.map((account) => (
        <label
          key={account.id}
          className="flex cursor-pointer items-center gap-1.5 whitespace-nowrap"
        >
          <input
            type="checkbox"
            checked={selected.includes(account.id)}
            onChange={() => onToggle(account)}
          />
          <span
            className="size-2 rounded-full"
            style={{ backgroundColor: account.color }}
          />
          {account.name}
        </label>
      ))}
    </div>
  )
}

function PerformancePanel({
  performance,
  from,
  to,
  benchmark,
  setFrom,
  setTo,
  setBenchmark,
}: {
  performance: ReturnType<typeof useAnalyticsPerformance>
  from: string
  to: string
  benchmark: string
  setFrom: (value: string) => void
  setTo: (value: string) => void
  setBenchmark: (value: string) => void
}) {
  const data = performance.data
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 pt-4">
          <div>
            <Label htmlFor="analytics-from">Du</Label>
            <Input
              id="analytics-from"
              type="date"
              value={from}
              onChange={(event) => setFrom(event.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="analytics-to">Au</Label>
            <Input
              id="analytics-to"
              type="date"
              value={to}
              onChange={(event) => setTo(event.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="analytics-benchmark">Indice de comparaison</Label>
            <Input
              id="analytics-benchmark"
              value={benchmark}
              onChange={(event) =>
                setBenchmark(event.target.value.toUpperCase())
              }
              placeholder="^FCHI"
            />
          </div>
          <p className="pb-2 text-xs text-muted-foreground">
            CAC 40 : <code>^FCHI</code> · S&amp;P 500 : <code>^GSPC</code> ·
            ticker Yahoo personnalisé accepté.
          </p>
        </CardContent>
      </Card>
      {performance.isLoading ? (
        <LoadingCard />
      ) : data ? (
        <>
          <div className="grid gap-4 md:grid-cols-3">
            <Metric
              title="Performance TWR"
              value={rate(data.points.at(-1)?.twrPercent)}
              icon={<TrendingUp className="size-4" />}
            />
            <Metric
              title="Rendement annualisé"
              value={rate(data.annualizedReturn)}
            />
            <Metric
              title={`Indice ${data.benchmarkTicker}`}
              value={rate(data.points.at(-1)?.benchmarkPercent)}
            />
          </div>
          <Card>
            <CardHeader>
              <CardTitle>Performance nette des flux</CardTitle>
              <CardDescription>
                La TWR neutralise les versements et retraits ; les jours
                reconstruits restent signalés dans les données.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ChartContainer config={chartConfig} className="h-72 w-full">
                <LineChart data={data.points}>
                  <CartesianGrid vertical={false} />
                  <XAxis
                    dataKey="date"
                    tickFormatter={(value) => value.slice(5)}
                  />
                  <YAxis tickFormatter={(value) => `${value}%`} />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <Legend />
                  <Line
                    type="monotone"
                    dataKey="twrPercent"
                    stroke="var(--color-twrPercent)"
                    dot={false}
                    strokeWidth={2}
                  />
                  <Line
                    type="monotone"
                    dataKey="benchmarkPercent"
                    stroke="var(--color-benchmarkPercent)"
                    dot={false}
                    strokeWidth={2}
                    connectNulls
                  />
                </LineChart>
              </ChartContainer>
            </CardContent>
          </Card>
          <MonthlyHeatmap values={data.monthlyReturns} />
          {data.warnings.length > 0 && <Warnings items={data.warnings} />}
        </>
      ) : null}
    </div>
  )
}

function FirePanel({
  projection,
}: {
  projection: ReturnType<typeof useFire>["data"]
}) {
  const { data: saved } = useFireSettings()
  const update = useUpdateFireSettings()
  const [settings, setSettings] = useState<FireSettings | null>(null)
  useEffect(() => {
    if (saved) setSettings(saved)
  }, [saved])
  const change = (key: keyof FireSettings, value: number | string | null) =>
    setSettings((current) => (current ? { ...current, [key]: value } : current))
  if (!settings) return <LoadingCard />
  const submit = async () => {
    await update.mutateAsync(settings)
    toast.success("Hypothèses FIRE enregistrées.")
  }
  return (
    <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
      <Card>
        <CardHeader>
          <CardTitle>Objectif FIRE</CardTitle>
          <CardDescription>
            Le rendement observé utilise la TWR annualisée de tes comptes
            sélectionnés. En son absence, renseigne un taux manuel.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label="Dépenses annuelles (€)"
              value={settings.annualExpenses}
              onChange={(v) => change("annualExpenses", v)}
            />
            <Field
              label="Épargne mensuelle (€)"
              value={settings.monthlySavings}
              onChange={(v) => change("monthlySavings", v)}
            />
            <Field
              label="Taux de retrait sûr (%)"
              value={settings.safeWithdrawalRate * 100}
              onChange={(v) => change("safeWithdrawalRate", v / 100)}
            />
            <Field
              label="Rendement manuel (%)"
              value={(settings.manualReturnRate ?? 0) * 100}
              onChange={(v) => change("manualReturnRate", v / 100)}
            />
          </div>
          <div className="flex gap-2">
            <Button
              variant={
                settings.returnMode === "OBSERVED_TWR" ? "default" : "outline"
              }
              onClick={() => change("returnMode", "OBSERVED_TWR")}
            >
              Performance observée
            </Button>
            <Button
              variant={settings.returnMode === "MANUAL" ? "default" : "outline"}
              onClick={() => change("returnMode", "MANUAL")}
            >
              Hypothèse manuelle
            </Button>
          </div>
          <Button onClick={submit} disabled={update.isPending}>
            {update.isPending && (
              <Loader2 className="mr-2 size-4 animate-spin" />
            )}
            Enregistrer
          </Button>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Projection</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <p className="text-xs text-muted-foreground">Capital cible</p>
            <CurrencyDisplay
              value={projection?.fireNumber ?? 0}
              className="text-2xl font-bold"
            />
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Progression</p>
            <p className="text-xl font-semibold">
              {rate(projection?.progressPercent)}
            </p>
            <Progress
              value={Math.min(projection?.progressPercent ?? 0, 100)}
              className="mt-2"
            />
          </div>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <p className="text-muted-foreground">Rendement utilisé</p>
              <p>
                {rate(
                  projection?.annualReturnRate
                    ? projection.annualReturnRate * 100
                    : null
                )}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Date estimée</p>
              <p>{formatDate(projection?.estimatedFireDate)}</p>
            </div>
          </div>
          {!projection?.observedReturnAvailable &&
            settings.returnMode === "OBSERVED_TWR" && (
              <p className="text-xs text-amber-600">
                Pas encore assez d’historique observé : la projection attendra
                une TWR annualisée.
              </p>
            )}
        </CardContent>
      </Card>
    </div>
  )
}

function LotsPanel({
  lots,
  loading,
}: {
  lots: ReturnType<typeof usePortfolioLots>["data"]
  loading: boolean
}) {
  if (loading) return <LoadingCard />
  return (
    <Card>
      <CardHeader>
        <CardTitle>Lignes d’achat</CardTitle>
        <CardDescription>
          Chaque achat est présenté séparément. Les ventes sont ventilées au
          prorata des lots ouverts, afin de préserver le calcul historique au
          prix moyen.
        </CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table className="w-full min-w-[900px] text-sm">
          <thead className="border-b text-left text-muted-foreground">
            <tr>
              <th>Compte</th>
              <th>Valeur</th>
              <th>Acheté le</th>
              <th>Quantité restante</th>
              <th>PRU</th>
              <th>Valeur</th>
              <th>Plus-value</th>
            </tr>
          </thead>
          <tbody>
            {lots?.map((lot, index) => (
              <tr
                key={`${lot.accountId}-${lot.ticker}-${lot.purchaseDate}-${index}`}
                className="border-b last:border-0"
              >
                <td className="py-3">{lot.accountName}</td>
                <td>
                  <strong>{lot.ticker}</strong>
                  <br />
                  <span className="text-xs text-muted-foreground">
                    {lot.name}
                  </span>
                </td>
                <td>
                  {lot.purchaseDate
                    ? formatDate(lot.purchaseDate)
                    : "Date non disponible"}
                </td>
                <td>{number(lot.remainingQuantity)}</td>
                <td>
                  <CurrencyDisplay value={lot.costPerUnit} />
                </td>
                <td>
                  {lot.currentValue == null ? (
                    "—"
                  ) : (
                    <CurrencyDisplay value={lot.currentValue} />
                  )}
                </td>
                <td
                  className={
                    lot.pnl != null && lot.pnl >= 0
                      ? "text-emerald-600"
                      : "text-red-600"
                  }
                >
                  {lot.pnl == null ? (
                    "—"
                  ) : (
                    <>
                      {rate(lot.pnlPercent)} ·{" "}
                      <CurrencyDisplay value={lot.pnl} />
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {lots?.length === 0 && (
          <p className="py-6 text-center text-muted-foreground">
            Aucune ligne d’investissement détaillée.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function RebalancePanel({
  lines,
  total,
  loading,
}: {
  lines: RebalanceLine[]
  total: number
  loading: boolean
}) {
  const mutation = useSetRebalanceTargets()
  const [targets, setTargets] = useState<Record<string, string>>({})
  useEffect(
    () =>
      setTargets(
        Object.fromEntries(
          lines.map((line) => [
            `${line.accountId}:${line.ticker}`,
            String(line.targetPercent),
          ])
        )
      ),
    [lines]
  )
  if (loading) return <LoadingCard />
  const targetSum = Object.values(targets).reduce(
    (sum, value) => sum + (Number(value) || 0),
    0
  )
  const save = async () => {
    if (Math.abs(targetSum - 100) > 0.001) {
      toast.error("Les cibles doivent totaliser exactement 100 %.")
      return
    }
    await mutation.mutateAsync(
      lines.map((line) => ({
        accountId: line.accountId,
        ticker: line.ticker,
        targetPercent: Number(targets[`${line.accountId}:${line.ticker}`]) || 0,
      }))
    )
    toast.success("Cibles de rééquilibrage enregistrées.")
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Rééquilibrage</CardTitle>
        <CardDescription>
          Recommandations uniquement : Picsou ne passe aucun ordre. Valeur
          suivie : {formatCurrency(total)}.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-sm">
            <thead className="border-b text-left text-muted-foreground">
              <tr>
                <th>Ligne</th>
                <th>Actuel</th>
                <th>Cible (%)</th>
                <th>Écart</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => {
                const key = `${line.accountId}:${line.ticker}`
                return (
                  <tr key={key} className="border-b last:border-0">
                    <td className="py-3">
                      <strong>{line.ticker}</strong>
                      <span className="ml-2 text-muted-foreground">
                        {line.name}
                      </span>
                    </td>
                    <td>{rate(line.currentPercent)}</td>
                    <td>
                      <Input
                        className="h-8 w-24"
                        type="number"
                        min="0"
                        max="100"
                        step="0.01"
                        value={targets[key] ?? ""}
                        onChange={(event) =>
                          setTargets((current) => ({
                            ...current,
                            [key]: event.target.value,
                          }))
                        }
                      />
                    </td>
                    <td>
                      {line.differenceEur == null ? (
                        "—"
                      ) : (
                        <>
                          {line.differenceEur >= 0 ? "+" : ""}
                          {formatCurrency(line.differenceEur)} (
                          {number(line.quantityToTrade)})
                        </>
                      )}
                    </td>
                    <td
                      className={
                        line.action === "BUY"
                          ? "text-emerald-600"
                          : line.action === "SELL"
                            ? "text-red-600"
                            : ""
                      }
                    >
                      {line.action}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div className="flex items-center gap-3">
          <Button
            onClick={save}
            disabled={mutation.isPending || lines.length === 0}
          >
            {mutation.isPending && (
              <Loader2 className="mr-2 size-4 animate-spin" />
            )}
            Enregistrer les cibles
          </Button>
          <span
            className={
              Math.abs(targetSum - 100) < 0.001
                ? "text-emerald-600"
                : "text-amber-600"
            }
          >
            {targetSum.toFixed(2)} % / 100 %
          </span>
        </div>
      </CardContent>
    </Card>
  )
}

function BackfillPanel({
  accountIds,
  plan,
  loading,
}: {
  accountIds: number[]
  plan: ReturnType<typeof useBackfillPlan>["data"]
  loading: boolean
}) {
  const mutation = useBackfill()
  const [accountDates, setAccountDates] = useState<Record<number, string>>({})
  const [lineDates, setLineDates] = useState<Record<string, string>>({})
  useEffect(() => {
    if (plan)
      setAccountDates(
        Object.fromEntries(
          plan.accounts
            .filter((account) => account.suggestedStartDate)
            .map((account) => [account.accountId, account.suggestedStartDate!])
        )
      )
  }, [plan])
  if (loading) return <LoadingCard />
  const save = async () => {
    const missing =
      plan?.accounts.some(
        (account) => account.needsStartDate && !accountDates[account.accountId]
      ) ||
      plan?.accounts.some((account) =>
        account.unknownTickers.some(
          (ticker) => !lineDates[`${account.accountId}:${ticker}`]
        )
      )
    if (missing) {
      toast.error(
        "Renseigne chaque date de début nécessaire avant le backfill."
      )
      return
    }
    const result = await mutation.mutateAsync({
      accountIds,
      accountStartDates: accountDates,
      lineStartDates: lineDates,
    })
    toast.success(`${result.snapshotsCreated} snapshots créés.`)
    result.warnings.forEach((warning) => toast.warning(warning))
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Backfill historique</CardTitle>
        <CardDescription>
          Seuls les jours sans snapshot sont ajoutés et marqués « reconstruit »
          ou « estimé ». Une ligne n’est jamais valorisée avant sa propre date
          de départ.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {plan?.accounts.map((account) => (
          <div key={account.accountId} className="rounded-lg border p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <strong>{account.accountName}</strong>
                <p className="text-xs text-muted-foreground">
                  {account.suggestedStartDate
                    ? `Début déduit de la première transaction : ${formatDate(account.suggestedStartDate)}`
                    : "Aucune transaction : date de départ à fournir."}
                </p>
              </div>
              <div>
                <Label>Date du compte</Label>
                <Input
                  type="date"
                  value={accountDates[account.accountId] ?? ""}
                  onChange={(event) =>
                    setAccountDates((current) => ({
                      ...current,
                      [account.accountId]: event.target.value,
                    }))
                  }
                />
              </div>
            </div>
            {account.unknownTickers.map((ticker) => {
              const key = `${account.accountId}:${ticker}`
              return (
                <div
                  className="mt-3 flex flex-wrap items-center gap-3 border-t pt-3"
                  key={key}
                >
                  <span className="min-w-28 font-medium">{ticker}</span>
                  <span className="text-xs text-amber-600">
                    Pas d’achat importé : indique le début réel de cette ligne.
                  </span>
                  <Input
                    className="w-auto"
                    type="date"
                    value={lineDates[key] ?? ""}
                    onChange={(event) =>
                      setLineDates((current) => ({
                        ...current,
                        [key]: event.target.value,
                      }))
                    }
                  />
                </div>
              )
            })}
          </div>
        ))}
        <Button
          onClick={save}
          disabled={mutation.isPending || !plan?.accounts.length}
        >
          {mutation.isPending && (
            <Loader2 className="mr-2 size-4 animate-spin" />
          )}
          Créer les snapshots manquants
        </Button>
      </CardContent>
    </Card>
  )
}

function MonthlyHeatmap({ values }: { values: Record<string, number> }) {
  const entries = Object.entries(values)
  const color = (value: number) =>
    value >= 2
      ? "bg-emerald-600 text-white"
      : value > 0
        ? "bg-emerald-200 text-emerald-950"
        : value <= -2
          ? "bg-red-600 text-white"
          : value < 0
            ? "bg-red-200 text-red-950"
            : "bg-muted"
  return (
    <Card>
      <CardHeader>
        <CardTitle>Heatmap mensuelle</CardTitle>
        <CardDescription>
          Rendement TWR mensuel, après neutralisation des flux.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6 lg:grid-cols-12">
          {entries.map(([month, value]) => (
            <div
              key={month}
              title={month}
              className={`rounded-lg p-3 text-center text-xs ${color(value)}`}
            >
              <strong className="block">{month}</strong>
              {rate(value)}
            </div>
          ))}
        </div>
        {entries.length === 0 && (
          <p className="text-muted-foreground">Pas encore assez de données.</p>
        )}
      </CardContent>
    </Card>
  )
}

function Field({
  label,
  value,
  onChange,
}: {
  label: string
  value: number
  onChange: (value: number) => void
}) {
  return (
    <div>
      <Label>{label}</Label>
      <Input
        type="number"
        value={Number.isFinite(value) ? value : ""}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  )
}
function Metric({
  title,
  value,
  icon,
}: {
  title: string
  value: string
  icon?: ReactNode
}) {
  return (
    <Card>
      <CardContent className="pt-4">
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          {icon}
          {title}
        </p>
        <p className="mt-1 text-2xl font-bold">{value}</p>
      </CardContent>
    </Card>
  )
}
function Warnings({ items }: { items: string[] }) {
  return (
    <Card className="border-amber-500/30">
      <CardContent className="pt-4 text-sm text-amber-700">
        <ul className="list-disc space-y-1 pl-4">
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
function LoadingCard() {
  return (
    <Card>
      <CardContent className="flex min-h-40 items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 size-4 animate-spin" />
        Calcul en cours…
      </CardContent>
    </Card>
  )
}
