import { Suspense } from "react";

import { KnowledgeDetailPage } from "@/features/knowledge";

export default async function KnowledgeDatasetPage({
  params,
}: {
  params: Promise<{ datasetId: string }>;
}) {
  const { datasetId } = await params;
  // useSearchParams（?section= 深链）要求 Suspense 边界
  return (
    <Suspense fallback={null}>
      <KnowledgeDetailPage datasetId={datasetId} />
    </Suspense>
  );
}
