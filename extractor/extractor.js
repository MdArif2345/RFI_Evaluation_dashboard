// DOCX Q&A Extractor - Main Application
class DocxQAExtractor {
    constructor(root) {
        this.root = root;
        this.extractedData = [];
        this.filteredData = [];
        this.documentData = new Map();
        this.excelFile = null;
        this.approvedResults = new Set();
        this._corpusBusy = false;
        this.init();
    }

    $(id) {
        return this.root.querySelector("#ex-" + id);
    }

    aiEnabled() {
        const box = this.$("ai-scan-checkbox");
        return !!(box && box.checked);
    }

    init() {
        this.setupEventListeners();
        this.setupDragAndDrop();
    }

    setupEventListeners() {
        // File selection
        this.$('file-select-btn').addEventListener('click', () => {
            this.$('file-input').click();
        });

        this.$('file-input').addEventListener('change', (e) => {
            this.handleFiles(Array.from(e.target.files));
        });

        // Search functionality
        this.$('search-input').addEventListener('input', (e) => {
            this.filterResults(e.target.value);
        });

        // Copy TSV button
        this.$('copy-tsv-btn').addEventListener('click', () => {
            this.copyAsTSV();
        });

        // Reset button
        this.$('reset-btn').addEventListener('click', () => {
            this.reset();
        });

        this.$('ai-scan-checkbox').addEventListener('change', (e) => {
            const hint = this.$('ai-hint');
            if (hint) hint.style.display = e.target.checked ? 'block' : 'none';
        });

        this.$('check-ai-btn').addEventListener('click', () => {
            this.checkWithAI();
        });

        const addBtn = this.$('add-corpus-btn');
        if (addBtn) {
            addBtn.addEventListener('click', () => this.addToCorpus());
        }

        // Excel upload functionality
        this.$('excel-input').addEventListener('change', (e) => {
            this.handleExcelUpload(e.target.files[0]);
        });

        this.$('insert-btn').addEventListener('click', () => {
            this.insertIntoExcel();
        });

        this.$('download-btn').addEventListener('click', () => {
            this.downloadApprovedData();
        });

        // Excel remove functionality
        this.$('excel-remove-btn').addEventListener('click', () => {
            this.removeExcelFile();
        });
    }

    setupDragAndDrop() {
        const dropzone = this.$('dropzone');

        dropzone.addEventListener('dragenter', (e) => {
            e.preventDefault();
            dropzone.classList.add('dragover');
        });

        dropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropzone.classList.add('dragover');
        });

        dropzone.addEventListener('dragleave', (e) => {
            e.preventDefault();
            if (!dropzone.contains(e.relatedTarget)) {
                dropzone.classList.remove('dragover');
            }
        });

        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('dragover');
            
            const files = Array.from(e.dataTransfer.files).filter(file => 
                file.name.toLowerCase().endsWith('.docx')
            );

            if (files.length === 0) {
                this.showToast('Please drop only DOCX files', 'warning');
                return;
            }

            this.handleFiles(files);
        });
    }

    async handleFiles(files) {
        if (files.length === 0) return;

        // Check if this is a large batch (more than 10 files)
        const isLargeBatch = files.length > 10;
        const batchSize = isLargeBatch ? 5 : files.length; // Process in smaller batches for large sets

        // Show file list container
        this.$('file-list-container').style.display = 'block';
        this.$('dropzone').classList.add('processing');

        const fileList = this.$('file-list');
        fileList.innerHTML = '';

        // Create progress bar for large batches
        let progressContainer = null;
        let progressBar = null;
        let progressText = null;
        
        if (isLargeBatch) {
            progressContainer = this.createProgressBar();
            fileList.appendChild(progressContainer);
        }

        let processedCount = 0;
        let successCount = 0;
        let errorCount = 0;
        const processingLog = [];

        // Process files in batches
        for (let i = 0; i < files.length; i += batchSize) {
            const batch = files.slice(i, i + batchSize);
            
            if (isLargeBatch) {
                this.showNotification(`📦 Processing batch ${Math.floor(i / batchSize) + 1}/${Math.ceil(files.length / batchSize)} (${batch.length} files)`, 'info');
                this.updateProgressBar(progressContainer, processedCount, files.length, `Processing batch ${Math.floor(i / batchSize) + 1}...`);
            }

            // Process batch in parallel with concurrency limit
            const batchPromises = batch.map(async (file) => {
                const fileItem = this.createFileItem(file);
                fileList.appendChild(fileItem);

                try {
                    const startTime = Date.now();
                    const result = await this.parseDocxFile(file);
                    const processingTime = Date.now() - startTime;
                    
                    this.updateFileStatus(fileItem, 'success', `${result.qaPairs.length} Q&A pairs found`);
                    
                    // Store document data grouped by filename with timestamp to allow duplicates
                    const uniqueKey = `${file.name}_${Date.now()}`;
                    this.documentData.set(uniqueKey, {
                        metadata: result.metadata,
                        qaPairs: result.qaPairs,
                        filename: file.name,
                        uniqueKey: uniqueKey
                    });
                    
                    // Add to extracted data for backward compatibility with search/copy
                    result.qaPairs.forEach(qa => {
                        this.extractedData.push({
                            registrationName: result.metadata.registrationName || '',
                            filename: file.name,
                            country: result.metadata.country || '',
                            smLm: result.metadata.smLm || '',
                            question: qa.question,
                            response: qa.response,
                            codes: qa.codes || '',
                            keywords: qa.keywords || ''
                        });
                    });

                    // Log successful processing
                    processingLog.push({
                        filename: file.name,
                        status: 'success',
                        qaPairs: result.qaPairs.length,
                        processingTime: processingTime,
                        size: file.size
                    });
                    
                    successCount++;
                    return { success: true, file: file.name, qaPairs: result.qaPairs.length };

                } catch (error) {
                    console.error('Error processing file:', file.name, error);
                    this.updateFileStatus(fileItem, 'error', 'Processing failed');
                    
                    // Log failed processing
                    processingLog.push({
                        filename: file.name,
                        status: 'error',
                        error: error.message,
                        size: file.size
                    });
                    
                    errorCount++;
                    return { success: false, file: file.name, error: error.message };
                }
            });

            // Wait for batch to complete
            const batchResults = await Promise.all(batchPromises);
            processedCount += batch.length;

            // Update progress for large batches
            if (isLargeBatch) {
                this.updateProgressBar(progressContainer, processedCount, files.length, `Processed ${processedCount}/${files.length} files`);
                
                // Log batch completion
                const batchSuccessCount = batchResults.filter(r => r.success).length;
                const batchErrorCount = batchResults.filter(r => !r.success).length;
                this.showNotification(`✅ Batch ${Math.floor(i / batchSize) + 1} completed: ${batchSuccessCount} success, ${batchErrorCount} errors`, 
                    batchErrorCount > 0 ? 'warning' : 'success');
            }

            // Small delay between batches to prevent overwhelming the system
            if (i + batchSize < files.length) {
                await new Promise(resolve => setTimeout(resolve, 100));
            }
        }

        // Remove progress bar
        if (progressContainer) {
            progressContainer.remove();
        }

        // Update UI
        this.$('dropzone').classList.remove('processing');
        this.$('dropzone').classList.add('success');
        
        // Show comprehensive results
        if (this.extractedData.length > 0) {
            this.displayResults();
            this.updateExcelControls();
        }

        // Show detailed completion summary
        this.showBatchProcessingSummary(files.length, successCount, errorCount, processingLog);
    }

    createProgressBar() {
        const progressContainer = document.createElement('div');
        progressContainer.className = 'progress-container';
        progressContainer.innerHTML = `
            <div class="progress-header">
                <h4>📊 Batch Processing Progress</h4>
                <span class="progress-text">Initializing...</span>
            </div>
            <div class="progress-bar-wrapper">
                <div class="progress-bar">
                    <div class="progress-fill"></div>
                </div>
                <span class="progress-percentage">0%</span>
            </div>
        `;
        return progressContainer;
    }

    updateProgressBar(container, current, total, text) {
        const percentage = Math.round((current / total) * 100);
        const progressFill = container.querySelector('.progress-fill');
        const progressText = container.querySelector('.progress-text');
        const progressPercentage = container.querySelector('.progress-percentage');
        
        progressFill.style.width = `${percentage}%`;
        progressText.textContent = text;
        progressPercentage.textContent = `${percentage}%`;
    }

    showBatchProcessingSummary(totalFiles, successCount, errorCount, processingLog) {
        const totalQAPairs = processingLog
            .filter(log => log.status === 'success')
            .reduce((sum, log) => sum + log.qaPairs, 0);
        
        const avgProcessingTime = processingLog
            .filter(log => log.status === 'success' && log.processingTime)
            .reduce((sum, log, _, arr) => sum + log.processingTime / arr.length, 0);

        let summaryMessage = `📊 Batch Processing Complete!\n\n`;
        summaryMessage += `📁 Total files: ${totalFiles}\n`;
        summaryMessage += `✅ Successfully processed: ${successCount}\n`;
        summaryMessage += `❌ Failed: ${errorCount}\n`;
        summaryMessage += `📝 Total Q&A pairs extracted: ${totalQAPairs}\n`;
        
        if (avgProcessingTime > 0) {
            summaryMessage += `⏱️ Average processing time: ${Math.round(avgProcessingTime)}ms per file\n`;
        }

        if (errorCount > 0) {
            summaryMessage += `\n❌ Failed files:\n`;
            processingLog
                .filter(log => log.status === 'error')
                .forEach(log => {
                    summaryMessage += `  • ${log.filename}: ${log.error}\n`;
                });
        }

        // Show success rate
        const successRate = Math.round((successCount / totalFiles) * 100);
        summaryMessage += `\n📈 Success rate: ${successRate}%`;

        // Log detailed summary to console for debugging
        console.log('Batch Processing Summary:', {
            totalFiles,
            successCount,
            errorCount,
            totalQAPairs,
            avgProcessingTime: Math.round(avgProcessingTime),
            successRate,
            detailedLog: processingLog
        });

        this.showToast(summaryMessage, errorCount > 0 ? 'warning' : 'success');
    }

    createFileItem(file) {
        const fileItem = document.createElement('div');
        fileItem.className = 'file-item';
        fileItem.innerHTML = `
            <div class="file-info">
                <div class="file-icon">📄</div>
                <div class="file-details">
                    <h4>${file.name}</h4>
                    <p>${this.formatFileSize(file.size)}</p>
                </div>
            </div>
            <div class="file-status">
                <div class="status-badge status-processing">
                    <div class="loading-spinner"></div>
                    Processing
                </div>
            </div>
        `;
        return fileItem;
    }

    updateFileStatus(fileItem, status, message) {
        const statusElement = fileItem.querySelector('.file-status');
        const statusClass = `status-${status}`;
        const statusText = status === 'success' ? 'Success' : status === 'error' ? 'Error' : 'Processing';
        
        statusElement.innerHTML = `
            <div class="status-badge ${statusClass}">${statusText}</div>
            <div class="qa-count">${message}</div>
        `;
    }

    async parseDocxFile(file) {
        try {
            // Load the DOCX file as a ZIP
            const zip = await JSZip.loadAsync(file);
            
            // Extract metadata from headers
            const metadata = await this.extractMetadata(zip);
            
            // Extract Q&A pairs from document body
            const qaPairs = await this.extractQAPairs(zip, file.name);
            
            return { metadata, qaPairs };
        } catch (error) {
            // Enhanced error notification with specific details
            const errorMessage = `Failed to parse ${file.name}: ${error.message}`;
            this.showNotification(`❌ ${errorMessage}`, 'error');
            console.error('DOCX parsing error:', {
                filename: file.name,
                fileSize: file.size,
                error: error.message,
                stack: error.stack
            });
            throw new Error(errorMessage);
        }
    }

    async extractMetadata(zip) {
        const metadata = {
            registrationName: '',
            country: '',
            smLm: ''
        };

        try {
            // Try to read header files
            const headerFiles = Object.keys(zip.files).filter(name => 
                name.startsWith('word/header') && name.endsWith('.xml')
            );

            for (const headerFile of headerFiles) {
                const headerContent = await zip.files[headerFile].async('text');
                const headerText = this.extractTextFromXML(headerContent);
                
                // Extract registration name (product/strength)
                if (!metadata.registrationName) {
                    metadata.registrationName = this.extractRegistrationName(headerText);
                }
                
                // Extract country
                if (!metadata.country) {
                    metadata.country = this.extractCountry(headerText);
                }
                
                // Extract SM/LM
                if (!metadata.smLm) {
                    metadata.smLm = this.extractSMLM(headerText);
                }
            }

            // Fallback: try document.xml if headers don't contain metadata
            if (!metadata.registrationName || !metadata.country) {
                const docFile = zip.files['word/document.xml'];
                if (docFile) {
                    const docContent = await docFile.async('text');
                    const docText = this.extractTextFromXML(docContent);
                    
                    if (!metadata.registrationName) {
                        metadata.registrationName = this.extractRegistrationName(docText);
                    }
                    if (!metadata.country) {
                        metadata.country = this.extractCountry(docText);
                    }
                    if (!metadata.smLm) {
                        metadata.smLm = this.extractSMLM(docText);
                    }
                }
            }

            // Fallback to structured data (Content Controls)
            if (!metadata.registrationName || !metadata.country) {
                const sdtMetadata = await this.extractMetadataFromContentControls(zip);
                if (!metadata.country && sdtMetadata.country) {
                    metadata.country = sdtMetadata.country;
                }
                if (!metadata.registrationName && sdtMetadata.registrationName) {
                    metadata.registrationName = sdtMetadata.registrationName;
                }
            }

        } catch (error) {
            console.warn('Error extracting metadata:', error);
        }

        return metadata;
    }

    extractRegistrationName(text) {
        // Look for product names with strength patterns
        const patterns = [
            /([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(\d+(?:\.\d+)?\s*mg)/i,
            /([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(\d+(?:\.\d+)?\s*%)/i,
            /([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(\d+(?:\.\d+)?\s*g)/i,
            /Product:\s*([^,\n\r]+)/i,
            /Registration:\s*([^,\n\r]+)/i,
            /Brand:\s*([^,\n\r]+)/i
        ];

        for (const pattern of patterns) {
            const match = text.match(pattern);
            if (match) {
                return match[1].trim() + (match[2] ? ' ' + match[2].trim() : '');
            }
        }

        // Fallback: look for capitalized words at the beginning
        const lines = text.split('\n');
        for (const line of lines.slice(0, 10)) {
            const trimmed = line.trim();
            if (trimmed.length > 3 && /^[A-Z]/.test(trimmed) && !/^(Question|Response|Bayer|Dear|Subject)/.test(trimmed)) {
                const words = trimmed.split(/[\s\-–]+/);
                if (words.length <= 4 && words[0].length > 2) {
                    return words.slice(0, 2).join(' ');
                }
            }
        }

        return '';
    }

    extractCountry(text) {
        // Common country patterns
        const countryPatterns = [
            /Country:\s*([^,\n\r]+)/i,
            /([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*[–\-]\s*\d{4}/,
            /^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*[–\-]/m
        ];

        for (const pattern of countryPatterns) {
            const match = text.match(pattern);
            if (match) {
                const country = match[1].trim();
                // Validate it looks like a country name
                if (country.length > 2 && country.length < 30 && /^[A-Z][a-z]/.test(country)) {
                    return country;
                }
            }
        }

        // Look for known country names
        const knownCountries = [
            'Germany', 'France', 'Italy', 'Spain', 'Netherlands', 'Belgium', 'Austria',
            'Switzerland', 'United Kingdom', 'Ireland', 'Denmark', 'Sweden', 'Norway',
            'Finland', 'Poland', 'Czech Republic', 'Hungary', 'Slovakia', 'Slovenia',
            'Croatia', 'Romania', 'Bulgaria', 'Greece', 'Portugal', 'Luxembourg',
            'United States', 'Canada', 'Mexico', 'Brazil', 'Argentina', 'Chile',
            'Colombia', 'Peru', 'Venezuela', 'Ecuador', 'Uruguay', 'Paraguay',
            'Japan', 'China', 'South Korea', 'India', 'Australia', 'New Zealand',
            'Singapore', 'Malaysia', 'Thailand', 'Philippines', 'Indonesia',
            'South Africa', 'Egypt', 'Morocco', 'Nigeria', 'Kenya', 'Ghana'
        ];

        for (const country of knownCountries) {
            if (text.includes(country)) {
                return country;
            }
        }

        return '';
    }

    extractSMLM(text) {
        const patterns = [
            /\b(SM|LM)\b/g,
            /Small\s+Molecule/i,
            /Large\s+Molecule/i
        ];

        for (const pattern of patterns) {
            const match = text.match(pattern);
            if (match) {
                const result = match[0].toUpperCase();
                if (result === 'SM' || result === 'LM') {
                    return result;
                }
                if (result.includes('SMALL')) return 'SM';
                if (result.includes('LARGE')) return 'LM';
            }
        }

        return '';
    }

    async extractMetadataFromContentControls(zip) {
        const sdtData = {
            country: '',
            registrationName: '',
            registrationNameLong: ''
        };

        const filesToSearch = ['word/document.xml', ...Object.keys(zip.files).filter(name => name.startsWith('word/header') && name.endsWith('.xml'))];

        const mappings = {
            country: ['choose country', 'country', 'market'],
            registrationName: ['add tradename', 'tradename'],
            registrationNameLong: ['add registration name / pharmdoss', 'registration name', 'product name']
        };

        for (const filePath of filesToSearch) {
            if (!zip.files[filePath]) continue;

            const xmlContent = await zip.files[filePath].async('text');
            
            const sdtRegex = /<w:sdt>(.*?)<\/w:sdt>/g;
            let sdtMatch;
            while ((sdtMatch = sdtRegex.exec(xmlContent)) !== null) {
                const sdtBlock = sdtMatch[1];

                const aliasRegex = /<w:alias w:val="(.*?)"\/>/;
                const tagRegex = /<w:tag w:val="(.*?)"\/>/;
                
                const aliasMatch = sdtBlock.match(aliasRegex);
                const tagMatch = sdtBlock.match(tagRegex);
                
                const controlName = (aliasMatch && aliasMatch[1]) || (tagMatch && tagMatch[1]);

                if (controlName) {
                    const lowerControlName = controlName.toLowerCase();
                    
                    const contentRegex = /<w:sdtContent>(.*?)<\/w:sdtContent>/;
                    const contentMatch = sdtBlock.match(contentRegex);
                    
                    if (contentMatch && contentMatch[1]) {
                        const text = this.extractTextFromXML(contentMatch[1]);

                        if (text) {
                            for (const [key, aliases] of Object.entries(mappings)) {
                                if (aliases.some(alias => lowerControlName.includes(alias))) {
                                    if (!sdtData[key]) {
                                        sdtData[key] = text;
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        
        return {
            country: sdtData.country,
            registrationName: sdtData.registrationName || sdtData.registrationNameLong
        };
    }

    async extractQAPairs(zip, filename = '') {
        try {
            const docFile = zip.files['word/document.xml'];
            if (!docFile) {
                throw new Error('Document content not found');
            }

            const docContent = await docFile.async('text');
            const text = this.extractStructuredTextFromXML(docContent);
            
            const aiScanEnabled = this.aiEnabled();
            
            if (aiScanEnabled) {
                try {
                    this.showNotification(`AI processing: ${filename}`, 'info');
                    
                    let headerText = '';
                    const headerFiles = Object.keys(zip.files).filter(name => 
                        name.startsWith('word/header') && name.endsWith('.xml')
                    );
                    
                    for (const headerFile of headerFiles) {
                        const headerContent = await zip.files[headerFile].async('text');
                        headerText += this.extractTextFromXML(headerContent) + '\n';
                    }
                    
                    const aiResult = await this.extractWithAI(text, filename, headerText.trim());
                    this.showNotification(`AI extraction completed for ${filename}`, 'success');
                    return aiResult.qaPairs;
                } catch (error) {
                    console.warn('AI extraction failed, falling back to rule-based parsing:', error);
                    this.showNotification('AI extraction failed, using fallback parsing', 'warning');
                }
            }
            
            const qaPairs = this.parseQAPairs(text);
            
            if (aiScanEnabled && this.shouldTriggerAIFallback(qaPairs)) {
                return await this.handleAIFallback(zip, filename, text);
            }
            
            return qaPairs;
        } catch (error) {
            throw new Error(`Failed to extract Q&A pairs: ${error.message}`);
        }
    }

    shouldTriggerAIFallback(qaPairs) {
        // Trigger AI fallback if no questions found or sequence is incomplete
        if (qaPairs.length === 0) {
            return true;
        }
        
        // Check for incomplete sequence
        const numbers = qaPairs.map(qa => qa.questionNumber).sort((a, b) => a - b);
        if (numbers.length > 1) {
            const minNum = Math.min(...numbers);
            const maxNum = Math.max(...numbers);
            const expectedLength = maxNum - minNum + 1;
            
            // If we're missing more than 20% of expected questions, trigger AI
            if (numbers.length < expectedLength * 0.8) {
                return true;
            }
        }
        
        return false;
    }

    async handleAIFallback(zip, filename, text) {
        if (!this.aiEnabled()) {
            return [];
        }
        
        try {
            this.showNotification(`No questions found with standard parsing. Trying AI extraction for ${filename}...`, 'info');
            
            // Extract header text for AI context
            let headerText = '';
            const headerFiles = Object.keys(zip.files).filter(name => 
                name.startsWith('word/header') && name.endsWith('.xml')
            );
            
            for (const headerFile of headerFiles) {
                const headerContent = await zip.files[headerFile].async('text');
                headerText += this.extractTextFromXML(headerContent) + '\n';
            }
            
            const aiResult = await this.extractWithAI(text, filename, headerText.trim());
            
            if (aiResult.qaPairs.length > 0) {
                this.showNotification(`✅ AI extraction successful! Found ${aiResult.qaPairs.length} Q&A pairs in ${filename}`, 'success');
            } else {
                this.showNotification(`⚠️ AI extraction completed but no Q&A pairs found in ${filename}`, 'warning');
            }
            
            return aiResult.qaPairs;
        } catch (error) {
            console.error('AI fallback failed:', error);
            this.showNotification(`❌ AI fallback failed for ${filename}: ${error.message}`, 'error');
            return [];
        }
    }

    parseQAPairs(text) {
        // First try the original "Question x" syntax
        let qaPairs = this.parseQAPairsWithQuestionSyntax(text);
        
        // If no results found, try the "Comment x" syntax
        if (qaPairs.length === 0) {
            qaPairs = this.parseQAPairsWithCommentSyntax(text);
        }
        
        return qaPairs;
    }

    parseQAPairsWithQuestionSyntax(text) {
        const qaPairs = [];
        
        // Follow the pseudo code exactly:
        // 1) Search for "Question"
        // 2) Output everything after "Question" until "Bayer Response to" comes as Question
        // 3) Output everything after "Bayer Response to" until "Question" comes as Response
        // 4) Make sure that the script checks for a number after "Question x" and "Bayer Response to Question x"
        
        // Find all "Question X" patterns that are likely section headers
        // Use context clues to distinguish headers from in-text references
        const questionMatches = [];
        const questionPattern = /Question\s+(\d+)/gi;
        let match;
        
        while ((match = questionPattern.exec(text)) !== null) {
            const questionNumber = parseInt(match[1]);
            const matchStart = match.index;
            const matchEnd = match.index + match[0].length;
            
            // Get context around the match (50 chars before and after)
            const beforeText = text.substring(Math.max(0, matchStart - 50), matchStart);
            const afterText = text.substring(matchEnd, Math.min(text.length, matchEnd + 50));
            
            // Skip if this is part of "Bayer Response to Question X"
            if (/Bayer\s+Response\s+to\s*$/i.test(beforeText)) {
                continue;
            }
            
            // Simple check for double digits: look at the two characters after "Question "
            // If both are digits, it's a double digit number
            // If one is digit and one is not, it's a single digit number
            const questionText = match[0]; // "Question X" or "Question XX"
            const numberPart = match[1]; // The captured number
            
            // Check if this is likely an in-text reference by looking for common reference words
            const isInTextReference = 
                /\b(see|refer|mentioned|above|below|in|to|from|of|the)\s*$/i.test(beforeText) ||
                /^\s*(above|below|shows|indicates|demonstrates|states)/i.test(afterText);
            
            if (!isInTextReference) {
                questionMatches.push({
                    number: questionNumber,
                    startIndex: matchStart,
                    fullMatch: match[0]
                });
            }
        }
        
        // Sort by position in document
        questionMatches.sort((a, b) => a.startIndex - b.startIndex);
        
        // Process each question
        for (let i = 0; i < questionMatches.length; i++) {
            const currentQuestion = questionMatches[i];
            
            // Find the next sequential question number (currentQuestion.number + 1)
            const expectedNextNumber = currentQuestion.number + 1;
            let nextQuestion = null;
            
            // Look for the next question with the expected sequential number
            for (let j = i + 1; j < questionMatches.length; j++) {
                if (questionMatches[j].number === expectedNextNumber) {
                    nextQuestion = questionMatches[j];
                    break;
                }
            }
            
            // If no sequential next question found, use the next question in document order
            if (!nextQuestion && i + 1 < questionMatches.length) {
                nextQuestion = questionMatches[i + 1];
            }
            
            // Start after "Question X"
            const questionStartIndex = currentQuestion.startIndex + currentQuestion.fullMatch.length;
            
            // Find the end boundary (next sequential question or end of document)
            const sectionEndIndex = nextQuestion ? nextQuestion.startIndex : text.length;
            const fullSection = text.substring(questionStartIndex, sectionEndIndex);
            
            // Look for "Bayer Response to Question X" specifically for this question number (handles double digits)
            // Use word boundary to ensure exact number matching
            const bayerResponsePattern = new RegExp(`Bayer\\s+Response\\s+to\\s+Question\\s+${currentQuestion.number}\\b`, 'i');
            const bayerMatch = fullSection.match(bayerResponsePattern);
            
            if (bayerMatch) {
                const bayerStartIndex = fullSection.indexOf(bayerMatch[0]);
                
                // Question: everything from start of section until "Bayer Response to Question X"
                let questionText = fullSection.substring(0, bayerStartIndex);
                questionText = this.cleanKeepBreaks(questionText);
                
                // Response: everything after "Bayer Response to Question X" until end of section
                const responseStartIndex = bayerStartIndex + bayerMatch[0].length;
                let responseText = fullSection.substring(responseStartIndex);
                
                // Clean response - remove any colon and whitespace at the start
                responseText = responseText.replace(/^\s*:?\s*/, '');
                
                // Remove "Source:" sections if present
                const sourceIndex = responseText.search(/\n\s*Source\s*:/i);
                if (sourceIndex !== -1) {
                    responseText = responseText.substring(0, sourceIndex);
                }
                
                responseText = this.cleanKeepBreaks(responseText);
                
                // Always add the Q&A pair with both question and response
                if (questionText.length > 5) {
                    let finalQuestionNumber = currentQuestion.number;
                    let finalQuestionText = questionText;
                    let finalResponseText = responseText || '';
                    
                    // Check if both question and response start with "0"
                    if (finalQuestionText.startsWith('0') && finalResponseText.startsWith('0')) {
                        // Remove leading "0" from both question and response
                        finalQuestionText = finalQuestionText.substring(1).trim();
                        finalResponseText = finalResponseText.substring(1).trim();
                        // Add zero to question number (multiply by 10)
                        finalQuestionNumber = finalQuestionNumber * 10;
                    }
                    
                    // Extract codes and keywords
                    const codes = this.extractCodes(finalQuestionText + ' ' + finalResponseText);
                    const keywords = this.extractKeywords(finalQuestionText + ' ' + finalResponseText);
                    
                    qaPairs.push({
                        questionNumber: finalQuestionNumber,
                        question: finalQuestionText,
                        response: finalResponseText,
                        codes: codes,
                        keywords: keywords
                    });
                }
            } else {
                // Edge case: No "Bayer Response to Question X" found
                // Still add as Q&A pair but with empty response
                let questionText = this.cleanKeepBreaks(fullSection);
                
                if (questionText.length > 5) {
                    // Extract codes and keywords
                    const codes = this.extractCodes(questionText);
                    const keywords = this.extractKeywords(questionText);
                    
                    qaPairs.push({
                        questionNumber: currentQuestion.number,
                        question: questionText,
                        response: '',
                        codes: codes,
                        keywords: keywords
                    });
                }
            }
        }
        
        // Sort by question number
        qaPairs.sort((a, b) => a.questionNumber - b.questionNumber);
        
        // Remove duplicates and merge text for same question numbers
        const uniqueQAPairs = [];
        const questionMap = new Map();
        
        for (const pair of qaPairs) {
            const questionNum = pair.questionNumber;
            
            if (questionMap.has(questionNum)) {
                // Question number already exists, append text to existing entry
                const existing = questionMap.get(questionNum);
                
                // Append question text if it's different and meaningful
                if (pair.question && pair.question.length > 10 && 
                    !existing.question.includes(pair.question.substring(0, 50))) {
                    existing.question += ' ' + pair.question;
                }
                
                // Append response text if it's different and meaningful
                if (pair.response && pair.response.length > 10 && 
                    !existing.response.includes(pair.response.substring(0, 50))) {
                    existing.response += ' ' + pair.response;
                }
                
                // Clean up the merged text
                existing.question = this.cleanKeepBreaks(existing.question);
                existing.response = this.cleanKeepBreaks(existing.response);
                
            } else {
                // New question number, add to map and unique list
                questionMap.set(questionNum, pair);
                uniqueQAPairs.push(pair);
            }
        }
        
        return uniqueQAPairs;
    }

    parseQAPairsWithCommentSyntax(text) {
        const qaPairs = [];
        
        // Similar logic but for "Comment x" and "Bayer Response to Question x" pattern
        const commentMatches = [];
        const commentPattern = /Comment\s+(\d+)/gi;
        let match;
        
        while ((match = commentPattern.exec(text)) !== null) {
            const commentNumber = parseInt(match[1]);
            const matchStart = match.index;
            const matchEnd = match.index + match[0].length;
            
            // Get context around the match (50 chars before and after)
            const beforeText = text.substring(Math.max(0, matchStart - 50), matchStart);
            const afterText = text.substring(matchEnd, Math.min(text.length, matchEnd + 50));
            
            // Skip if this is part of "Bayer Response to Question X"
            if (/Bayer\s+Response\s+to\s*$/i.test(beforeText)) {
                continue;
            }
            
            // Check if this is likely an in-text reference by looking for common reference words
            const isInTextReference = 
                /\b(see|refer|mentioned|above|below|in|to|from|of|the)\s*$/i.test(beforeText) ||
                /^\s*(above|below|shows|indicates|demonstrates|states)/i.test(afterText);
            
            if (!isInTextReference) {
                commentMatches.push({
                    number: commentNumber,
                    startIndex: matchStart,
                    fullMatch: match[0]
                });
            }
        }
        
        // Sort by position in document
        commentMatches.sort((a, b) => a.startIndex - b.startIndex);
        
        // Process each comment
        for (let i = 0; i < commentMatches.length; i++) {
            const currentComment = commentMatches[i];
            
            // Find the next sequential comment number (currentComment.number + 1)
            const expectedNextNumber = currentComment.number + 1;
            let nextComment = null;
            
            // Look for the next comment with the expected sequential number
            for (let j = i + 1; j < commentMatches.length; j++) {
                if (commentMatches[j].number === expectedNextNumber) {
                    nextComment = commentMatches[j];
                    break;
                }
            }
            
            // If no sequential next comment found, use the next comment in document order
            if (!nextComment && i + 1 < commentMatches.length) {
                nextComment = commentMatches[i + 1];
            }
            
            // Start after "Comment X"
            const commentStartIndex = currentComment.startIndex + currentComment.fullMatch.length;
            
            // Find the end boundary (next sequential comment or end of document)
            const sectionEndIndex = nextComment ? nextComment.startIndex : text.length;
            const fullSection = text.substring(commentStartIndex, sectionEndIndex);
            
            // Look for "Bayer Response to Question X" specifically for this comment number
            // Note: Response still uses "Question" even when the comment uses "Comment"
            const bayerResponsePattern = new RegExp(`Bayer\\s+Response\\s+to\\s+Question\\s+${currentComment.number}\\b`, 'i');
            const bayerMatch = fullSection.match(bayerResponsePattern);
            
            if (bayerMatch) {
                const bayerStartIndex = fullSection.indexOf(bayerMatch[0]);
                
                // Question: everything from start of section until "Bayer Response to Question X"
                let questionText = fullSection.substring(0, bayerStartIndex);
                questionText = this.cleanKeepBreaks(questionText);
                
                // Response: everything after "Bayer Response to Question X" until end of section
                const responseStartIndex = bayerStartIndex + bayerMatch[0].length;
                let responseText = fullSection.substring(responseStartIndex);
                
                // Clean response - remove any colon and whitespace at the start
                responseText = responseText.replace(/^\s*:?\s*/, '');
                
                // Remove "Source:" sections if present
                const sourceIndex = responseText.search(/\n\s*Source\s*:/i);
                if (sourceIndex !== -1) {
                    responseText = responseText.substring(0, sourceIndex);
                }
                
                responseText = this.cleanKeepBreaks(responseText);
                
                // Always add the Q&A pair with both question and response
                if (questionText.length > 5) {
                    let finalQuestionNumber = currentComment.number;
                    let finalQuestionText = questionText;
                    let finalResponseText = responseText || '';
                    
                    // Check if both question and response start with "0"
                    if (finalQuestionText.startsWith('0') && finalResponseText.startsWith('0')) {
                        // Remove leading "0" from both question and response
                        finalQuestionText = finalQuestionText.substring(1).trim();
                        finalResponseText = finalResponseText.substring(1).trim();
                        // Add zero to question number (multiply by 10)
                        finalQuestionNumber = finalQuestionNumber * 10;
                    }
                    
                    // Extract codes and keywords
                    const codes = this.extractCodes(finalQuestionText + ' ' + finalResponseText);
                    const keywords = this.extractKeywords(finalQuestionText + ' ' + finalResponseText);
                    
                    qaPairs.push({
                        questionNumber: finalQuestionNumber,
                        question: finalQuestionText,
                        response: finalResponseText,
                        codes: codes,
                        keywords: keywords
                    });
                }
            } else {
                // Edge case: No "Bayer Response to Question X" found
                // Still add as Q&A pair but with empty response
                let questionText = this.cleanKeepBreaks(fullSection);
                
                if (questionText.length > 5) {
                    // Extract codes and keywords
                    const codes = this.extractCodes(questionText);
                    const keywords = this.extractKeywords(questionText);
                    
                    qaPairs.push({
                        questionNumber: currentComment.number,
                        question: questionText,
                        response: '',
                        codes: codes,
                        keywords: keywords
                    });
                }
            }
        }
        
        // Sort by question number
        qaPairs.sort((a, b) => a.questionNumber - b.questionNumber);
        
        // Remove duplicates and merge text for same question numbers
        const uniqueQAPairs = [];
        const questionMap = new Map();
        
        for (const pair of qaPairs) {
            const questionNum = pair.questionNumber;
            
            if (questionMap.has(questionNum)) {
                // Question number already exists, append text to existing entry
                const existing = questionMap.get(questionNum);
                
                // Append question text if it's different and meaningful
                if (pair.question && pair.question.length > 10 && 
                    !existing.question.includes(pair.question.substring(0, 50))) {
                    existing.question += ' ' + pair.question;
                }
                
                // Append response text if it's different and meaningful
                if (pair.response && pair.response.length > 10 && 
                    !existing.response.includes(pair.response.substring(0, 50))) {
                    existing.response += ' ' + pair.response;
                }
                
                // Clean up the merged text
                existing.question = this.cleanKeepBreaks(existing.question);
                existing.response = this.cleanKeepBreaks(existing.response);
                
            } else {
                // New question number, add to map and unique list
                questionMap.set(questionNum, pair);
                uniqueQAPairs.push(pair);
            }
        }
        
        return uniqueQAPairs;
    }

    decodeXmlEntities(text) {
        const entityMap = {
            '&lt;': '<',
            '&gt;': '>',
            '&amp;': '&',
            '&quot;': '"',
            '&apos;': "'",
            '&nbsp;': ' ',
            '&ndash;': '\u2013',
            '&mdash;': '\u2014',
            '&hellip;': '\u2026',
            '&lsquo;': '\u2018',
            '&rsquo;': '\u2019',
            '&ldquo;': '\u201C',
            '&rdquo;': '\u201D'
        };
        let out = String(text || '');
        for (const [entity, char] of Object.entries(entityMap)) {
            out = out.replace(new RegExp(entity, 'g'), char);
        }
        out = out.replace(/&#(\d+);/g, (_, dec) => String.fromCharCode(parseInt(dec, 10)));
        out = out.replace(/&#x([0-9A-Fa-f]+);/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
        return out;
    }

    extractStructuredTextFromXML(xmlContent) {
        try {
            const parser = new DOMParser();
            const doc = parser.parseFromString(xmlContent, 'text/xml');
            if (doc.querySelector('parsererror')) {
                throw new Error('xml parse error');
            }
            const ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';
            const body = doc.getElementsByTagNameNS(ns, 'body')[0] || doc.documentElement;
            const kids = (el, name) => [...el.childNodes].filter((n) => n.nodeType === 1 && n.localName === name);
            const paraText = (el) => {
                const texts = el.getElementsByTagNameNS(ns, 't');
                let s = '';
                for (const t of texts) s += t.textContent || '';
                return this.decodeXmlEntities(s).replace(/\u00A0/g, ' ').trim();
            };
            const lines = [];
            for (const child of body.childNodes) {
                if (child.nodeType !== 1) continue;
                if (child.localName === 'p') {
                    const t = paraText(child);
                    if (t) lines.push(t);
                } else if (child.localName === 'tbl') {
                    for (const tr of kids(child, 'tr')) {
                        const cells = kids(tr, 'tc').map(paraText);
                        if (cells.some(Boolean)) lines.push(cells.join(' | '));
                    }
                    lines.push('');
                }
            }
            return lines.join('\n');
        } catch (_) {
            return this.extractTextFromXML(xmlContent);
        }
    }

    extractTextFromXML(xmlContent) {
        // Remove XML tags and extract text content
        let text = xmlContent.replace(/<[^>]*>/g, ' ');
        
        // Decode all XML/HTML entities comprehensively
        const entityMap = {
            '&lt;': '<',
            '&gt;': '>',
            '&amp;': '&',
            '&quot;': '"',
            '&apos;': "'",
            '&nbsp;': ' ',
            '&ndash;': '\u2013',
            '&mdash;': '\u2014',
            '&hellip;': '\u2026',
            '&lsquo;': '\u2018',
            '&rsquo;': '\u2019',
            '&ldquo;': '\u201C',
            '&rdquo;': '\u201D',
            '&trade;': '\u2122',
            '&reg;': '\u00AE',
            '&copy;': '\u00A9',
            '&euro;': '\u20AC',
            '&pound;': '\u00A3',
            '&yen;': '\u00A5',
            '&deg;': '\u00B0',
            '&plusmn;': '\u00B1',
            '&micro;': '\u00B5',
            '&para;': '\u00B6',
            '&sect;': '\u00A7',
            '&dagger;': '\u2020',
            '&Dagger;': '\u2021',
            '&bull;': '\u2022',
            '&middot;': '\u00B7'
        };
        
        // Replace named entities
        for (const [entity, char] of Object.entries(entityMap)) {
            text = text.replace(new RegExp(entity, 'g'), char);
        }
        
        // Decode numeric character references (&#123; and &#xAB;)
        text = text.replace(/&#(\d+);/g, (match, dec) => {
            return String.fromCharCode(parseInt(dec, 10));
        });
        
        text = text.replace(/&#x([0-9A-Fa-f]+);/g, (match, hex) => {
            return String.fromCharCode(parseInt(hex, 16));
        });
        
        // Handle Unicode characters properly
        // Normalize Unicode to NFC (Canonical Decomposition, followed by Canonical Composition)
        if (typeof text.normalize === 'function') {
            text = text.normalize('NFC');
        }
        
        return this.cleanText(text);
    }

    cleanText(text) {
        return text
            .replace(/\s+/g, ' ')
            .replace(/\u00AD/g, '') // Remove soft hyphens
            .replace(/[\r\n]+/g, ' ')
            .trim();
    }

    cleanKeepBreaks(text) {
        return String(text || '')
            .replace(/\u00AD/g, '')
            .replace(/[ \t]+/g, ' ')
            .replace(/ *\n */g, '\n')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    }

    extractCodes(text) {
        const codes = new Set();

        // Specific patterns
        const patterns = [
            /\b\d\.\d\.[A-Z]\.\d(?:[a-zA-Z0-9\-\.]*)/g, // e.g., 3.2.P.1
            /\b[A-Z]\.\d{2}\.\d{2}-\d{2,5}\b/g, // e.g., T.01.02-05
            /\b[A-Z]\.\d{2}\.\d{2}#\d+\b/g, // e.g., T.01.02#001699748
        ];

        patterns.forEach(p => {
            const matches = text.match(p);
            if (matches) {
                matches.forEach(m => codes.add(m));
            }
        });

        // Extract from "Source:" section
        const sourceMatch = text.match(/Source:\s*([\s\S]*)/i);
        if (sourceMatch && sourceMatch[1]) {
            const sourceLines = sourceMatch[1].split('\n').map(line => line.trim()).filter(line => line && line.length > 5);
            sourceLines.forEach(line => {
                codes.add(line);
            });
        }

        return Array.from(codes).join('; ');
    }

    extractKeywords(text) {
        // Predefined list of regulatory keywords
        const keywordsList = [
            'Description and Composition of the Drug Product', 'Batch formula', 'specification', 
            'Batch Analyses', 'justification of specification', 'Description of Manufacturing Process and Process Controls',
            'Control of Materials', 'Manufacturing Process Development', 'Container closure system',
            'Process Validation', 'analytical procedures', 'analytical test method DS', 'impurity B',
            'Characterization of impurities', 'related substances', 'response factor', 'stability data DS',
            'CEP', 'Validation Report', 'DP specification', 'validation parameter linearity',
            'stress conditions', 'LOD/LOQ', 'test method', 'Degradation Products', 'validation data',
            'assay and impurities', 'USP monograph', 'drug substance specification', 'batch data DS',
            'validation test method DS', 'justification of specification DS', 'justification of specification DP',
            'analytical development of test method', 'test method DP', 'drug product specification',
            'analytical method validation', 'batch data', 'raw data', 'Shelf Life', 'QC testing',
            'CoAs', 'chromatograms', 'LoQ', 'system suitability test', 'HPLC-method', 'method validation',
            'DS specification', 'consecutive batches', 'reference substance for impurity',
            'Stability Summary and Conclusion', 'Stability data', 'manufacturer', 'facilities and equipment',
            'controls of critical steps and intermediates', 'Batch Report', 'Validation of Analytical procedures',
            'Characterisation of Impurities', 'Reference Standards or Materials', 'Validation of Specification',
            'overages', 'nomenclature', 'General Properties', 'Analytical Procedures',
            'Drug Product', 'Components of the Drug Product', 'Manufacturer', 'Batch Formula',
            'Container Closure System', 'Facilities and Equipment', 'Product Quality Review',
            'Validation Master Plan', 'Batch Record - Master', 'Executed Batch Record',
            'drug substance manufacturer', 'ICHQ3D', 'elemental impurities', 'related substances',
            'assay', 'residual solvents', 'Impurities', 'Specification', 'Nitrosamine Risk Assessment',
            'Comparative Batch Analysis', 'Storage Condition', 'Site Responsibilities',
            'Method Transfer', 'Equipment Specifics', 'Comparative dissolution', 'nitrosamine',
            'GMP inspection', 'ETO sterilization', 'identity test in DP specification',
            'QP Declaration', 'excipient name', 'Medical Device', 'micronization site',
            'Support documents', 'Dissolution Protocol', 'Stability Protocol', 'Stability Commitment',
            'Declaration', 'risk assessment', 'manufacturing equipment', 'manufacturing description',
            'in-process control', 'dissolution', 'coating process', 'manufacturing site',
            'packaging material specification', 'validation of test method', 'shelf life specification',
            'Test Procedure', 'Release Specification', 'Evaluation of Stability Data',
            'Validation and Analytical Procedures', 'Specifications', 'Compatibility', 'pharmacopeia',
            'Sterility Testing', 'COA', 'stability data DP', 'GMP certificate', 'approval letter of variation',
            'validation of assay', 'standard solution', 'accuracy of analytical method', 'precision',
            'Control of Drug Product', 'Analytical Data', 'Justification of Specification',
            'root cause evaluation', 'Starting material', 'synthesis', 'Manufacturing Description',
            'Validation of the Manufacturing Process', 'Evaluation of the Production Process',
            'Sterilization', 'Filtration and holding time', 'Critical production areas',
            'IR spectrum', 'Stopper', 'Excipient Supplier', 'Chromatogram', 'CEP Certificate',
            'device DMF', 'letter of access', 'Nitrosamine impurities', 'Clarity and Color test',
            'stability studies', 'Validation of Tests for Assay and Impurities', 'in vivo mutagenicity',
            'study', 'HA Approval', 'compatibility', 'Production Equipment Team',
            'Microbiological Attributes', 'Certificate of Suitability', 'skip testing', 'EU-reliance',
            'Analytical validation', 'stability testing', 'Compendial monograph', 'specification value',
            'Excipient Specifications', 'workshare procedure', 'microbiological test method',
            'dissolution profile', 'comparability testing', 'Site responsibilities',
            'Manufacturing Flow-Chart', 'Specification Value', 'Content Uniformity',
            'Stability Indicating Test', 'Compendial Method', 'Risk Assessment', 'flow chart',
            'US-DMF', 'TSE/BSE statement', 'Formulation development', 'Secondary packaging',
            'degradation products', 'stability study', 'pharmaceutical form', 'microbial purity test',
            'pharmaceutical form and container of DP', 'concentration', 'statement to formula',
            'genotoxic impurities', 'Impurities Validation', 'holding time', 'Intermediates',
            'Annex III', 'Annex II', 'Annex I', 'Non-critical process step', 'Blend homogeneity',
            'DUNS number', 'Reprocessing', 'Potential process impurities', 'Impurities justification',
            'Packaging supplier', 'stability commitment', 'Active Substance Amount', 'Comparative Table',
            'Tests Removed', 'Regulatory Compliance', 'Stability Study Storage Position',
            'QP declaration', 'primary packaging material', 'stability report', 'stability data',
            'In-use stability', 'Bioburden Control Strategy', 'Endotoxin Tests', 'ASMF',
            'Manufacturing License'
        ];

        const foundKeywords = new Set();
        const textLower = text.toLowerCase();

        // Search for keywords in the text
        keywordsList.forEach(keyword => {
            const keywordLower = keyword.toLowerCase();
            if (textLower.includes(keywordLower)) {
                foundKeywords.add(keyword);
            }
        });

        // Return top 7 most relevant keywords (or all if less than 7)
        const keywordsArray = Array.from(foundKeywords);
        return keywordsArray.slice(0, 7).join('; ');
    }

    displayResults() {
        this.filteredData = [...this.extractedData];
        this.renderDocuments();
        this.$('results-container').style.display = 'block';
    }

    renderDocuments() {
        const container = this.$('documents-container');
        container.innerHTML = '';

        // Group data by document
        const documentGroups = new Map();
        
        for (const [filename, docData] of this.documentData) {
            // Filter Q&A pairs based on search
            const searchTerm = this.$('search-input').value.toLowerCase();
            let filteredQAPairs = docData.qaPairs;
            
            if (searchTerm) {
                filteredQAPairs = docData.qaPairs.filter(qa =>
                    qa.question.toLowerCase().includes(searchTerm) ||
                    qa.response.toLowerCase().includes(searchTerm) ||
                    (docData.metadata.registrationName || "").toLowerCase().includes(searchTerm) ||
                    (docData.metadata.country || "").toLowerCase().includes(searchTerm) ||
                    (docData.filename || "").toLowerCase().includes(searchTerm)
                );
            }
            
            if (filteredQAPairs.length > 0) {
                documentGroups.set(filename, {
                    ...docData,
                    qaPairs: filteredQAPairs
                });
            }
        }

        // Render each document
        for (const [filename, docData] of documentGroups) {
            const documentElement = this.createDocumentElement(filename, docData);
            container.appendChild(documentElement);
        }

        if (documentGroups.size === 0) {
            container.innerHTML = '<p style="text-align: center; color: #6c757d; padding: 40px;">No documents match your search criteria.</p>';
        }
    }

    formatReadableText(text) {
        if (!text) return '';
        let s = String(text).replace(/\u00A0/g, ' ').replace(/[ \t]+/g, ' ').trim();
        s = s.replace(/([.!?])([A-Z])/g, '$1 $2');
        s = s.replace(/([.!?])\s+/g, '$1\n\n');
        return s.replace(/\n{3,}/g, '\n\n').trim();
    }

    chipList(value) {
        if (!value) return '';
        return String(value)
            .split(/[;|]+/)
            .map((part) => part.trim())
            .filter(Boolean)
            .map((part) => `<span class="ex-chip">${this.escapeHtml(part)}</span>`)
            .join('');
    }

    renderClampedBlock(label, text, emptyLabel) {
        const readable = this.formatReadableText(text);
        if (!readable) {
            return `<div class="ex-qa-block"><div class="ex-qa-label">${label}</div><p class="no-response">${emptyLabel}</p></div>`;
        }
        const long = readable.length > 280 || readable.split('\n').length > 4;
        return `
            <div class="ex-qa-block">
                <div class="ex-qa-label">${label}</div>
                <div class="ex-qa-text${long ? ' is-clamped' : ''}">${this.escapeHtml(readable)}</div>
                ${long ? '<button type="button" class="ex-more">Show more</button>' : ''}
            </div>`;
    }

    createDocumentElement(filename, docData) {
        const documentDiv = document.createElement('div');
        documentDiv.className = 'document-item expanded';
        
        const metadata = docData.metadata;
        const qaPairs = docData.qaPairs;
        const displayName = docData.filename || filename;
        
        documentDiv.innerHTML = `
            <div class="document-header">
                <div class="document-controls">
                    <button class="delete-btn" title="Delete document" data-unique-key="${docData.uniqueKey}">✕</button>
                </div>
                <div class="document-info">
                    <div class="document-title">${this.escapeHtml(displayName)}</div>
                    <div class="document-meta">
                        ${metadata.registrationName ? `${this.escapeHtml(metadata.registrationName)} • ` : ''}
                        ${metadata.country ? `${this.escapeHtml(metadata.country)} • ` : ''}
                        ${metadata.smLm ? `${this.escapeHtml(metadata.smLm)} • ` : ''}
                        ${qaPairs.length} Q&A pair${qaPairs.length !== 1 ? 's' : ''}
                    </div>
                </div>
                <div class="document-stats">
                    <div class="expand-icon">▶</div>
                </div>
            </div>
            <div class="document-content">
                ${qaPairs.map((qa) => `
                    <article class="ex-qa">
                        <div class="ex-qa-top">
                            <span class="ex-qa-num">#${qa.questionNumber || ''}</span>
                            <div class="ex-chips">${this.chipList(qa.codes)}${this.chipList(qa.keywords)}</div>
                        </div>
                        ${this.renderClampedBlock('Question', qa.question, 'No question found')}
                        ${this.renderClampedBlock('Response', qa.response, 'No response found')}
                    </article>
                `).join('')}
            </div>
        `;

        const documentInfo = documentDiv.querySelector('.document-info');
        const expandIcon = documentDiv.querySelector('.expand-icon');
        
        const toggleExpand = (e) => {
            e.stopPropagation();
            documentDiv.classList.toggle('expanded');
        };
        
        documentInfo.addEventListener('click', toggleExpand);
        expandIcon.addEventListener('click', toggleExpand);

        const deleteBtn = documentDiv.querySelector('.delete-btn');
        deleteBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const uniqueKey = e.target.getAttribute('data-unique-key');
            if (confirm(`Are you sure you want to delete "${displayName}"?`)) {
                this.deleteDocument(uniqueKey);
            }
        });

        documentDiv.querySelectorAll('.ex-more').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const block = btn.previousElementSibling;
                block.classList.toggle('is-clamped');
                btn.textContent = block.classList.contains('is-clamped') ? 'Show more' : 'Show less';
            });
        });

        return documentDiv;
    }

    renderTable() {
        const tbody = this.$('results-tbody');
        tbody.innerHTML = '';

        this.filteredData.forEach(row => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td title="${this.escapeHtml(row.registrationName)}">${this.escapeHtml(row.registrationName)}</td>
                <td title="${this.escapeHtml(row.filename)}">${this.escapeHtml(row.filename)}</td>
                <td title="${this.escapeHtml(row.country)}">${this.escapeHtml(row.country)}</td>
                <td title="${this.escapeHtml(row.smLm)}">${this.escapeHtml(row.smLm)}</td>
                <td class="question-cell">${this.escapeHtml(row.question)}</td>
                <td class="response-cell">${this.escapeHtml(row.response)}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    filterResults(searchTerm) {
        if (!searchTerm.trim()) {
            this.filteredData = [...this.extractedData];
        } else {
            const term = searchTerm.toLowerCase();
            this.filteredData = this.extractedData.filter(row =>
                row.registrationName.toLowerCase().includes(term) ||
                row.filename.toLowerCase().includes(term) ||
                row.country.toLowerCase().includes(term) ||
                row.smLm.toLowerCase().includes(term) ||
                row.question.toLowerCase().includes(term) ||
                row.response.toLowerCase().includes(term) ||
                (row.codes && row.codes.toLowerCase().includes(term)) ||
                (row.keywords && row.keywords.toLowerCase().includes(term))
            );
        }
        // Re-render documents with filtered data
        this.renderDocuments();
    }

    async copyAsTSV() {
        if (this.filteredData.length === 0) {
            this.showToast('No data to copy', 'warning');
            return;
        }

        // Create TSV content without headers
        const tsvContent = [
            ['Registration Name', 'Filename', 'Country', 'SM/LM', 'Question', 'Response', 'Codes', 'Keywords'].join('\t'),
            ...this.filteredData.map(row => [
                this.escapeTSV(row.registrationName),
                this.escapeTSV(row.filename),
                this.escapeTSV(row.country),
                this.escapeTSV(row.smLm),
                this.escapeTSV(this.formatReadableText(row.question), true),
                this.escapeTSV(this.formatReadableText(row.response), true),
                this.escapeTSV(row.codes || ''),
                this.escapeTSV(row.keywords || '')
            ].join('\t'))
        ].join('\n');

        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(tsvContent);
                this.showToast(`Copied ${this.filteredData.length} rows to clipboard`, 'success');
            } else {
                // Fallback for older browsers
                const textArea = document.createElement('textarea');
                textArea.value = tsvContent;
                document.body.appendChild(textArea);
                textArea.select();
                document.execCommand('copy');
                document.body.removeChild(textArea);
                this.showToast(`Copied ${this.filteredData.length} rows to clipboard`, 'success');
            }
        } catch (error) {
            console.error('Copy failed:', error);
            this.showToast('Failed to copy to clipboard', 'error');
        }
    }

    escapeTSV(text, keepNewlines = false) {
        const value = String(text || '').replace(/\t/g, ' ');
        if (keepNewlines) {
            return value.replace(/\r\n/g, '\n').trim();
        }
        return value.replace(/[\r\n]+/g, ' ').trim();
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    truncateText(text, maxLength) {
        if (text.length <= maxLength) return text;
        return text.substring(0, maxLength) + '...';
    }

    formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;

        const container = this.$('toast-container');
        if (!container) return;
        container.appendChild(toast);

        // Auto remove after 5 seconds
        setTimeout(() => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        }, 5000);

        // Remove on click
        toast.addEventListener('click', () => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        });
    }

    showNotification(message, type = 'info') {
        // Use toast for notifications but with shorter duration for processing updates
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;

        const container = this.$('toast-container');
        if (!container) return;
        container.appendChild(toast);

        // Auto remove after 3 seconds for notifications
        setTimeout(() => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        }, 3000);

        // Remove on click
        toast.addEventListener('click', () => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        });
    }

    reset() {
        // Clear ALL data structures
        this.extractedData = [];
        this.filteredData = [];
        this.documentData.clear(); // Clear the Map that was causing persistence
        this.approvedResults.clear(); // Clear approved results
        
        // Reset UI
        this.$('file-list-container').style.display = 'none';
        this.$('results-container').style.display = 'none';
        this.$('file-input').value = '';
        this.$('search-input').value = '';
        
        // Reset dropzone
        const dropzone = this.$('dropzone');
        dropzone.classList.remove('processing', 'success', 'dragover');
        
        // Clear file list
        const fileList = this.$('file-list');
        if (fileList) {
            fileList.innerHTML = '';
        }
        
        // Clear documents container
        const documentsContainer = this.$('documents-container');
        if (documentsContainer) {
            documentsContainer.innerHTML = '';
        }
        
        this.showToast('Application reset - all data cleared', 'info');
    }

    async extractWithAI(text, filename, headerText = '') {
        const chunkSize = 12000;
        const chunks = this.chunkText(text, chunkSize);
        
        let allQAPairs = [];
        let metadata = { registrationName: '', country: '', smLm: '' };
        
        for (let i = 0; i < chunks.length; i++) {
            const chunk = chunks[i];
            const isFirstChunk = i === 0;

            try {
                const response = await fetch('/api/extract', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        text: chunk,
                        filename,
                        header_text: headerText,
                        chunk_index: i,
                        chunk_count: chunks.length
                    })
                });

                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || `API request failed for chunk ${i + 1}: ${response.status}`);
                }

                const result = await response.json();
                
                // Extract metadata from first chunk
                if (isFirstChunk && result.registrationName) {
                    metadata = {
                        registrationName: result.registrationName === 'not found' ? '' : result.registrationName || '',
                        country: result.country === 'not found' ? '' : result.country || '',
                        smLm: result.smLm === 'not found' ? '' : result.smLm || ''
                    };
                }
                
                // Collect Q&A pairs from all chunks
                if (result.qaPairs && Array.isArray(result.qaPairs)) {
                    allQAPairs.push(...result.qaPairs.map(qa => ({
                        questionNumber: qa.questionNumber || 0,
                        question: qa.question || '',
                        response: qa.response || '',
                        codes: qa.codes || '',
                        keywords: qa.keywords || ''
                    })));
                }

            } catch (error) {
                console.error(`AI extraction error for chunk ${i + 1}:`, error);
                // Continue with other chunks even if one fails
            }
        }
        
        // Remove duplicates and sort by question number
        const uniqueQAPairs = [];
        const seen = new Set();
        
        allQAPairs.sort((a, b) => a.questionNumber - b.questionNumber);
        
        for (const pair of allQAPairs) {
            const key = `${pair.questionNumber}`;
            if (!seen.has(key) && pair.question.length > 10) {
                seen.add(key);
                uniqueQAPairs.push(pair);
            }
        }
        
        return {
            metadata,
            qaPairs: uniqueQAPairs
        };
    }

    chunkText(text, maxChunkSize) {
        const chunks = [];
        let currentChunk = '';
        
        // Split by questions to avoid breaking Q&A pairs
        const questionSections = text.split(/(?=Question\s+\d+)/i);
        
        for (const section of questionSections) {
            if (currentChunk.length + section.length > maxChunkSize && currentChunk.length > 0) {
                // Current chunk is full, start a new one
                chunks.push(currentChunk.trim());
                currentChunk = section;
            } else {
                currentChunk += section;
            }
        }
        
        // Add the last chunk
        if (currentChunk.trim().length > 0) {
            chunks.push(currentChunk.trim());
        }
        
        // If no question sections found, split by character count
        if (chunks.length === 0) {
            for (let i = 0; i < text.length; i += maxChunkSize) {
                chunks.push(text.substring(i, i + maxChunkSize));
            }
        }
        
        return chunks;
    }

    async parseQAPairsWithAI(text, filename) {
        try {
            const aiResult = await this.extractWithAI(text, filename);
            return aiResult;
        } catch (error) {
            console.warn('AI extraction failed, falling back to rule-based parsing:', error);
            // Fallback to original parsing
            return {
                metadata: {
                    registrationName: this.extractRegistrationName(text),
                    country: this.extractCountry(text),
                    smLm: this.extractSMLM(text)
                },
                qaPairs: this.parseQAPairs(text)
            };
        }
    }

    async checkWithAI() {
        if (this.documentData.size === 0) {
            this.showToast('No documents to check. Please upload documents first.', 'warning');
            return;
        }

        const checkBtn = this.$('check-ai-btn');
        const originalText = checkBtn.textContent;
        checkBtn.textContent = '🤖 Checking...';
        checkBtn.disabled = true;

        try {
            // Analyze all documents
            let allResults = [];
            
            for (const [uniqueKey, docData] of this.documentData) {
                const extractedNumbers = docData.qaPairs.map(qa => qa.questionNumber).sort((a, b) => a - b);
                
                if (extractedNumbers.length === 0) {
                    allResults.push(`${docData.filename}: No questions found`);
                    continue;
                }
                
                const minNum = Math.min(...extractedNumbers);
                const maxNum = Math.max(...extractedNumbers);
                const expectedSequence = [];
                
                for (let i = minNum; i <= maxNum; i++) {
                    expectedSequence.push(i);
                }

                const missingNumbers = expectedSequence.filter(num => !extractedNumbers.includes(num));
                
                let result = `${docData.filename}:\n`;
                result += `  • Found: ${extractedNumbers.join(', ')}\n`;
                
                if (missingNumbers.length > 0) {
                    result += `  • Missing: ${missingNumbers.join(', ')}\n`;
                    result += `  • Check document for gaps`;
                } else {
                    result += `  • Complete sequence (${extractedNumbers.length} questions)`;
                }
                
                allResults.push(result);
            }
            
            const finalMessage = allResults.join('\n\n');
            const hasIssues = allResults.some(result => result.includes('Missing:'));
            
            this.showToast(finalMessage, hasIssues ? 'warning' : 'success');

        } catch (error) {
            console.error('AI check error:', error);
            this.showToast(`Check failed: ${error.message}`, 'error');
        } finally {
            checkBtn.textContent = originalText;
            checkBtn.disabled = false;
        }
    }

    // Excel upload and management methods
    async handleExcelUpload(file) {
        if (!file) return;
        
        if (!file.name.toLowerCase().endsWith('.xlsx') && !file.name.toLowerCase().endsWith('.xls')) {
            this.showToast('Please upload an Excel file (.xlsx or .xls)', 'warning');
            return;
        }

        this.excelFile = file;
        this.showToast(`Excel file "${file.name}" uploaded successfully`, 'success');
        
        // Show Excel file status
        this.showExcelFileStatus(file.name);
        
        // Update Excel controls
        this.updateExcelControls();
    }

    showExcelFileStatus(filename) {
        const fileStatus = this.$('excel-file-status');
        const fileName = this.$('excel-file-name');
        
        fileName.textContent = filename;
        fileStatus.style.display = 'block';
    }

    removeExcelFile() {
        this.excelFile = null;
        this.approvedResults.clear();
        
        // Hide Excel file status
        this.$('excel-file-status').style.display = 'none';
        
        // Remove approval states from documents
        const documentItems = this.root.querySelectorAll('.document-item');
        documentItems.forEach(item => {
            item.classList.remove('pending-approval', 'approved');
        });
        
        // Reset Excel controls
        this.updateExcelControls();
        
        this.showToast('Excel file removed', 'info');
    }

    approveDocument(uniqueKey) {
        if (this.documentData.has(uniqueKey)) {
            const docData = this.documentData.get(uniqueKey);
            const filename = docData.filename;
            
            // Find the document element and mark as approved
            const documentItems = this.root.querySelectorAll('.document-item');
            documentItems.forEach(item => {
                const itemKey = item.querySelector('.approve-btn').getAttribute('data-unique-key');
                if (itemKey === uniqueKey) {
                    item.classList.remove('pending-approval');
                    item.classList.add('approved');
                }
            });
            
            // Add to approved results
            this.approvedResults.add(filename);
            
            // Update Excel controls
            this.updateExcelControls();
            
            this.showToast(`Document "${filename}" approved`, 'success');
        }
    }

    markResultsForApproval() {
        // Add yellow highlighting to all document items
        const documentItems = this.root.querySelectorAll('.document-item');
        documentItems.forEach(item => {
            item.classList.add('pending-approval');
        });
    }

    updateExcelControls() {
        const insertBtn = this.$('insert-btn');
        const downloadBtn = this.$('download-btn');
        
        // Insert button requires both Excel file and extracted data
        if (this.excelFile && this.extractedData.length > 0) {
            insertBtn.disabled = false;
            insertBtn.classList.remove('btn-disabled');
        } else {
            insertBtn.disabled = true;
            insertBtn.classList.add('btn-disabled');
        }
        
        // Download button enabled only when there are parsed questions
        if (this.extractedData.length > 0) {
            downloadBtn.disabled = false;
            downloadBtn.classList.remove('btn-disabled');
        } else {
            downloadBtn.disabled = true;
            downloadBtn.classList.add('btn-disabled');
        }

        const addBtn = this.$('add-corpus-btn');
        if (addBtn) {
            addBtn.disabled = this.extractedData.length === 0;
            if (this.extractedData.length > 0) addBtn.classList.remove('btn-disabled');
            else addBtn.classList.add('btn-disabled');
        }
    }

    approveResults() {
        // Mark all currently visible results as approved (green)
        const documentItems = this.root.querySelectorAll('.document-item');
        documentItems.forEach(item => {
            item.classList.remove('pending-approval');
            item.classList.add('approved');
            
            // Add to approved results set
            const filename = item.querySelector('.document-title').textContent;
            this.approvedResults.add(filename);
        });
        
        this.showToast('All visible results approved', 'success');
        this.updateExcelControls();
    }

    async insertIntoExcel() {
        if (!this.excelFile || this.extractedData.length === 0) {
            this.showToast('No data to insert. Please upload documents first.', 'warning');
            return;
        }

        try {
            // Read the Excel file with formatting preservation options
            const arrayBuffer = await this.excelFile.arrayBuffer();
            const workbook = XLSX.read(arrayBuffer, { 
                type: 'array',
                cellStyles: true,
                cellNF: true,
                cellHTML: false
            });
            
            // Look for the "List of Questions" sheet specifically
            let targetSheetName = null;
            for (const sheetName of workbook.SheetNames) {
                if (sheetName.toLowerCase().includes('list of questions') || 
                    sheetName.toLowerCase().includes('questions')) {
                    targetSheetName = sheetName;
                    break;
                }
            }
            
            // If no "List of Questions" sheet found, throw error
            if (!targetSheetName) {
                throw new Error('Excel file must contain a "List of Questions" sheet. Expected structure not found.');
            }
            
            const worksheet = workbook.Sheets[targetSheetName];
            
            // Get existing data to analyze structure
            const existingData = XLSX.utils.sheet_to_json(worksheet, { header: 1 });
            
            if (existingData.length === 0) {
                throw new Error('Excel sheet is empty. Cannot determine structure.');
            }
            
            // Find the header row by looking for our required columns
            const requiredColumns = ['Registration name', 'Filename', 'Country', 'SM/LM', 'Question', 'Response', 'Codes', 'Keywords'];
            let headerRowIndex = -1;
            let headerRow = null;
            
            // Look for the header row (check first 5 rows)
            for (let i = 0; i < Math.min(5, existingData.length); i++) {
                const row = existingData[i];
                if (row && requiredColumns.some(col => 
                    row.some(cell => cell && cell.toString().toLowerCase().includes(col.toLowerCase())))) {
                    headerRowIndex = i;
                    headerRow = row;
                    break;
                }
            }
            
            if (headerRowIndex === -1) {
                throw new Error(`Could not find header row with required columns: ${requiredColumns.join(', ')}`);
            }
            
            // Map our data columns to Excel columns based on header names
            const columnMapping = {};
            
            requiredColumns.forEach(requiredCol => {
                for (let col = 0; col < headerRow.length; col++) {
                    const cellValue = headerRow[col];
                    if (cellValue && cellValue.toString().toLowerCase().includes(requiredCol.toLowerCase())) {
                        columnMapping[requiredCol] = col;
                        break;
                    }
                }
            });
            
            // Check which columns we found
            const foundColumns = Object.keys(columnMapping);
            const missingColumns = requiredColumns.filter(col => !foundColumns.includes(col));
            
            if (foundColumns.length === 0) {
                throw new Error(`No matching columns found. Expected columns: ${requiredColumns.join(', ')}`);
            }
            
            if (missingColumns.length > 0) {
                console.warn(`Missing columns (will be skipped): ${missingColumns.join(', ')}`);
            }
            
            // Find the last row with data
            let lastRow = headerRowIndex;
            for (let i = existingData.length - 1; i > headerRowIndex; i--) {
                if (existingData[i] && existingData[i].some(cell => cell !== undefined && cell !== '' && cell !== null)) {
                    lastRow = i;
                    break;
                }
            }
            
            // Determine the maximum column count needed
            const maxExistingCol = Math.max(...headerRow.map((_, index) => index));
            const maxMappingCol = Math.max(...Object.values(columnMapping));
            const totalColumns = Math.max(maxExistingCol, maxMappingCol) + 1;
            
            // Get reference cell formatting from existing data rows (if any exist)
            let referenceCellStyle = null;
            if (lastRow > headerRowIndex) {
                // Use the last data row as reference for formatting
                const refRowNum = lastRow + 1; // 1-based indexing
                const refColNum = Math.min(...Object.values(columnMapping)) + 1; // 1-based indexing
                const refCellAddress = XLSX.utils.encode_cell({ r: refRowNum - 1, c: refColNum - 1 });
                if (worksheet[refCellAddress] && worksheet[refCellAddress].s) {
                    referenceCellStyle = worksheet[refCellAddress].s;
                }
            }
            
            // Add extracted data starting from the next row after last data
            let currentRow = lastRow + 1;
            
            for (const data of this.extractedData) {
                // Create row array with proper length to match existing structure
                const row = new Array(totalColumns).fill('');
                
                // Fill in the data at correct column positions (only for found columns)
                if (columnMapping['Registration name'] !== undefined) {
                    row[columnMapping['Registration name']] = data.registrationName || '';
                }
                if (columnMapping['Filename'] !== undefined) {
                    row[columnMapping['Filename']] = data.filename || '';
                }
                if (columnMapping['Country'] !== undefined) {
                    row[columnMapping['Country']] = data.country || '';
                }
                if (columnMapping['SM/LM'] !== undefined) {
                    row[columnMapping['SM/LM']] = data.smLm || '';
                }
                if (columnMapping['Question'] !== undefined) {
                    row[columnMapping['Question']] = this.formatReadableText(data.question || '');
                }
                if (columnMapping['Response'] !== undefined) {
                    row[columnMapping['Response']] = this.formatReadableText(data.response || '');
                }
                if (columnMapping['Codes'] !== undefined) {
                    row[columnMapping['Codes']] = data.codes || '';
                }
                if (columnMapping['Keywords'] !== undefined) {
                    row[columnMapping['Keywords']] = data.keywords || '';
                }
                
                // Add row to worksheet
                XLSX.utils.sheet_add_aoa(worksheet, [row], { origin: currentRow });
                
                // Apply formatting to new cells if we have reference formatting
                if (referenceCellStyle) {
                    for (let col = 0; col < totalColumns; col++) {
                        const cellAddress = XLSX.utils.encode_cell({ r: currentRow, c: col });
                        if (worksheet[cellAddress]) {
                            worksheet[cellAddress].s = { ...referenceCellStyle };
                        }
                    }
                }
                
                currentRow++;
            }
            
            // Update the worksheet range
            const range = XLSX.utils.decode_range(worksheet['!ref'] || 'A1');
            range.e.r = Math.max(range.e.r, currentRow - 1);
            range.e.c = Math.max(range.e.c, totalColumns - 1);
            worksheet['!ref'] = XLSX.utils.encode_range(range);
            
            // Preserve column widths if they exist
            if (worksheet['!cols']) {
                // Ensure column widths extend to cover all columns
                while (worksheet['!cols'].length < totalColumns) {
                    worksheet['!cols'].push({ wch: 20 }); // Default width
                }
            }
            
            // Preserve row heights if they exist
            if (worksheet['!rows']) {
                // Ensure row heights extend to cover all rows
                while (worksheet['!rows'].length < currentRow) {
                    worksheet['!rows'].push({ hpt: 15 }); // Default height
                }
            }
            
            // Generate the updated Excel file with formatting preservation
            const updatedArrayBuffer = XLSX.write(workbook, { 
                bookType: 'xlsx', 
                type: 'array',
                cellStyles: true,
                bookSST: true
            });
            
            // Create and download the updated file
            const blob = new Blob([updatedArrayBuffer], { 
                type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' 
            });
            
            const link = document.createElement('a');
            const url = URL.createObjectURL(blob);
            link.setAttribute('href', url);
            
            // Generate filename with timestamp
            const originalName = this.excelFile.name.replace(/\.[^/.]+$/, '');
            const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
            link.setAttribute('download', `${originalName}_updated_${timestamp}.xlsx`);
            
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            URL.revokeObjectURL(url);
            
            const insertedColumns = foundColumns.join(', ');
            const skippedInfo = missingColumns.length > 0 ? ` (Skipped columns: ${missingColumns.join(', ')})` : '';
            
            this.showToast(`Successfully added ${this.extractedData.length} rows to "${targetSheetName}" sheet. Inserted data for: ${insertedColumns}${skippedInfo}`, 'success');

        } catch (error) {
            console.error('Insert error:', error);
            this.showToast(`Failed to insert data into Excel: ${error.message}`, 'error');
        }
    }

    findColumnMapping(headers) {
        const mapping = {};
        
        // Common header variations for each field
        const headerPatterns = {
            registrationName: ['registration name', 'product name', 'registration', 'product', 'name'],
            filename: ['filename', 'file name', 'file', 'document'],
            country: ['country', 'region', 'authority'],
            smLm: ['sm/lm', 'sm-lm', 'type', 'molecule type', 'classification'],
            question: ['question', 'q', 'query'],
            response: ['response', 'answer', 'reply', 'bayer response']
        };
        
        headers.forEach((header, index) => {
            if (!header) return;
            
            const headerLower = header.toString().toLowerCase().trim();
            
            for (const [field, patterns] of Object.entries(headerPatterns)) {
                if (patterns.some(pattern => headerLower.includes(pattern))) {
                    mapping[field] = index;
                    break;
                }
            }
        });
        
        return mapping;
    }

    async downloadApprovedData() {
        if (this.extractedData.length === 0) {
            this.showToast('No data to download', 'warning');
            return;
        }

        try {
            // Create CSV content with headers for all extracted data
            const headers = ['Registration Name', 'Filename', 'Country', 'SM/LM', 'Question', 'Response', 'Codes', 'Keywords'];
            const csvContent = [
                headers.map((h) => this.escapeCSV(h, true)).join(','),
                ...this.extractedData.map(row => [
                    this.escapeCSV(row.registrationName, true),
                    this.escapeCSV(row.filename, true),
                    this.escapeCSV(row.country, true),
                    this.escapeCSV(row.smLm, true),
                    this.escapeCSV(this.formatReadableText(row.question), true),
                    this.escapeCSV(this.formatReadableText(row.response), true),
                    this.escapeCSV(row.codes || '', true),
                    this.escapeCSV(row.keywords || '', true)
                ].join(','))
            ].join('\r\n');

            const blob = new Blob(['\uFEFF' + csvContent], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            
            if (link.download !== undefined) {
                const url = URL.createObjectURL(blob);
                link.setAttribute('href', url);
                link.setAttribute('download', `qa_data_${new Date().toISOString().slice(0, 10)}.csv`);
                link.style.visibility = 'hidden';
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                
                this.showToast(`Downloaded ${this.extractedData.length} rows as CSV file`, 'success');
            } else {
                // Fallback for browsers that don't support download attribute
                this.showToast('Download not supported in this browser. Use "Insert into Excel" instead.', 'warning');
            }

        } catch (error) {
            console.error('Download error:', error);
            this.showToast(`Failed to download data: ${error.message}`, 'error');
        }
    }

    escapeCSV(text, alwaysQuote = false) {
        const value = String(text ?? '');
        const needsQuote = alwaysQuote || /[",\n\r]/.test(value);
        if (needsQuote) {
            return '"' + value.replace(/"/g, '""') + '"';
        }
        return value;
    }

    deleteDocument(uniqueKey) {
        if (this.documentData.has(uniqueKey)) {
            const docData = this.documentData.get(uniqueKey);
            const filename = docData.filename;
            
            // Remove from document data
            this.documentData.delete(uniqueKey);
            
            // Remove from extracted data
            this.extractedData = this.extractedData.filter(item => 
                !(item.filename === filename && item.registrationName === docData.metadata.registrationName)
            );
            
            // Remove from approved results
            this.approvedResults.delete(filename);
            
            // Re-render
            this.displayResults();
            this.showToast(`Document "${filename}" removed`, 'info');
        }
    }

    async addToCorpus() {
        const rows = this.filteredData.length ? this.filteredData : this.extractedData;
        if (!rows.length) {
            this.showToast('No extracted rows to add', 'warning');
            return;
        }
        if (this._corpusBusy) return;
        this._corpusBusy = true;
        const btn = this.$('add-corpus-btn');
        const original = btn ? btn.textContent : '';
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Adding…';
        }
        try {
            const res = await fetch('/api/corpus/append', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    rows: rows.map((row) => ({
                        registrationName: row.registrationName || '',
                        filename: row.filename || '',
                        country: row.country || '',
                        smLm: row.smLm || '',
                        question: row.question || '',
                        response: row.response || '',
                        codes: row.codes || '',
                        keywords: row.keywords || ''
                    }))
                })
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(data.detail || `Append failed (${res.status})`);
            }
            this.showToast(
                `Added ${data.added} rows` +
                (data.skipped_duplicates ? ` · skipped ${data.skipped_duplicates} duplicates` : '') +
                ` · corpus now ${data.total_rows}`,
                'success'
            );
            if (typeof window.reloadDashboardMetrics === 'function') {
                await window.reloadDashboardMetrics();
            } else {
                window.location.reload();
            }
        } catch (error) {
            console.error('Add to corpus failed:', error);
            this.showToast(`Add to corpus failed: ${error.message}`, 'error');
        } finally {
            this._corpusBusy = false;
            if (btn) {
                btn.disabled = this.extractedData.length === 0;
                btn.textContent = original || 'Add to corpus';
            }
        }
    }
}

window.CMCExtractor = {
    mount(rootEl) {
        if (!rootEl) return null;
        if (rootEl._cmcExtractor) return rootEl._cmcExtractor;
        rootEl._cmcExtractor = new DocxQAExtractor(rootEl);
        return rootEl._cmcExtractor;
    }
};
