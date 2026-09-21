import { KnowledgeDetailPage } from "@/features/knowledge";

export default async function KnowledgeDatasetPage({
  params,
}: {
  params: Promise<{ datasetId: string }>;
}) {
  const { datasetId } = await params;
  return <KnowledgeDetailPage datasetId={datasetId} />;
}
