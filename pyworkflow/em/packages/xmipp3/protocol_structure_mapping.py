# **************************************************************************
# *
# * Authors:     Mohsen Kazemi  (mkazemi@cnb.csic.es)
# *              C.O.S. Sorzano (coss@cnb.csic.es)
# *
# * Unidad de  Bioinformatica of Centro Nacional de Biotecnologia , CSIC
# *
# * This program is free software; you can redistribute it and/or modify
# * it under the terms of the GNU General Public License as published by
# * the Free Software Foundation; either version 2 of the License, or
# * (at your option) any later version.
# *
# * This program is distributed in the hope that it will be useful,
# * but WITHOUT ANY WARRANTY; without even the implied warranty of
# * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# * GNU General Public License for more details.
# *
# * You should have received a copy of the GNU General Public License
# * along with this program; if not, write to the Free Software
# * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA
# * 02111-1307  USA
# *
# *  All comments concerning this program package may be sent to the
# *  e-mail address 'jmdelarosa@cnb.csic.es'
# *
# **************************************************************************

import os
from glob import glob

import pyworkflow.em.metadata as md
import pyworkflow.em as em
import pyworkflow.protocol.params as params
from pyworkflow.em.packages.xmipp3.convert import getImageLocation
from pyworkflow.protocol.constants import LEVEL_ADVANCED
from pyworkflow.utils.path import cleanPattern, createLink, moveFile, copyFile, makePath, cleanPath
#from os.path import basename, exists
from pyworkflow.object import String
from pyworkflow.em.data import SetOfNormalModes
from pyworkflow.em.packages.xmipp3 import XmippMdRow
from pyworkflow.em.packages.xmipp3.pdb.protocol_pseudoatoms_base import XmippProtConvertToPseudoAtomsBase
import xmipp
from pyworkflow.em.packages.xmipp3.nma.protocol_nma_base import XmippProtNMABase, NMA_CUTOFF_REL
from pyworkflow.em.packages.xmipp3.protocol_align_volume import XmippProtAlignVolume
import matplotlib.pyplot as plt
from sklearn.decomposition import PCA
import numpy as np
from sklearn import manifold

class XmippProtStructureMapping(XmippProtConvertToPseudoAtomsBase,XmippProtNMABase, XmippProtAlignVolume):
    """ 
    Multivariate distance analysis of elastically aligned electron microscopy density maps
    for exploring pathways of conformational changes    
     """
    _label = 'structure mapping'
           
    #--------------------------- DEFINE param functions --------------------------------------------
    def _defineParams(self, form):
        
        form.addSection(label='Input')
        form.addParam('inputVolumes', params.MultiPointerParam, pointerClass='SetOfVolumes,Volume',  
                      label="Input volume(s)", important=True, 
                      help='Select one or more volumes (Volume or SetOfVolumes)\n'
                           'to be aligned againt the reference volume.')
                
        form.addSection(label='Pseudoatom')
        XmippProtConvertToPseudoAtomsBase._defineParams(self,form)
        self.getParam("pseudoAtomRadius").setDefault(2)
        self.getParam("pseudoAtomTarget").setDefault(2)
        
        form.addSection(label='Normal Mode Analysis')
        XmippProtNMABase._defineParamsCommon(self,form)
        
        form.addSection(label='Multidimensional Scaling') 
        form.addParam('numberOfDimensions', params.IntParam, default=2,
                      label="Number of dimensions",
                      help='In normal practice, it should be 1, 2 or, at most, 3.')        
        form.addParallelSection(threads=4, mpi=1)
        
    #--------------------------- INSERT steps functions --------------------------------------------    
    def _insertAllSteps(self):
        
        cutoffStr=''
        if self.cutoffMode == NMA_CUTOFF_REL:
            cutoffStr = 'Relative %f'%self.rcPercentage.get()
        else:
            cutoffStr = 'Absolute %f'%self.rc.get()

        maskArgs = ''
        alignArgs = self._getAlignArgs()
        ALIGN_ALGORITHM_EXHAUSTIVE_LOCAL = 1
        self.alignmentAlgorithm = ALIGN_ALGORITHM_EXHAUSTIVE_LOCAL       
                                
        volList = [vol.clone() for vol in self._iterInputVolumes()]
                                    
        for voli in volList:
            fnIn = getImageLocation(voli)
            fnMask = self._insertMaskStep(fnIn)
            suffix = "_%d"%voli.getObjId()        
            
            self._insertFunctionStep('convertToPseudoAtomsStep', fnIn, fnMask, voli.getSamplingRate(), suffix)
            fnPseudoAtoms = self._getPath("pseudoatoms_%d.pdb"%voli.getObjId())
            
            self._insertFunctionStep('computeModesStep', fnPseudoAtoms, self.numberOfModes, cutoffStr)
            self._insertFunctionStep('reformatOutputStep', os.path.basename(fnPseudoAtoms))
                                            
            self._insertFunctionStep('qualifyModesStep', self.numberOfModes, self.collectivityThreshold.get(), 
                                        self._getPath("pseudoatoms_%d.pdb"%voli.getObjId()), suffix)
            #rigid alignment            
            for volj in volList:
                if volj.getObjId() is not voli.getObjId():
                    refFn = getImageLocation(voli)
                    inVolFn = getImageLocation(volj)
                    outVolFn = self._getPath('outputRigidAlignment_vol_%d_to_%d.vol' % (volj.getObjId(), voli.getObjId()))
                    self._insertFunctionStep('alignVolumeStep', refFn, inVolFn, outVolFn, maskArgs, alignArgs)
                    
        
                 
        self._insertFunctionStep('gatherResultsStep')
                                        
    #--------------------------- STEPS functions --------------------------------------------
    
    def gatherResultsStep(self):
                
        volList = [vol.clone() for vol in self._iterInputVolumes()]
         
         
        #elastic alignment        
        for voli in volList:
            mdVols = xmipp.MetaData()
            files = glob(self._getPath('outputRigidAlignment_vol_*_to_%d.vol')%voli.getObjId())
            fnOutMeta = self._getExtraPath('RigidAlignToVol_%d.xmd')%voli.getObjId()
            for f in files:
                mdVols.setValue(xmipp.MDL_IMAGE, f, mdVols.addObject())      
            mdVols.write(fnOutMeta)
                                              
            fnPseudo = self._getPath("pseudoatoms_%d.pdb"%voli.getObjId())
            fnModes = self._getPath("modes_%d.xmd"%voli.getObjId())
            Ts = voli.getSamplingRate()
            fnDeform = self._getExtraPath("compDeformVol_%d.xmd"%voli.getObjId())
            sigma = Ts * self.pseudoAtomRadius.get() 
            self.runJob('xmipp_nma_alignment_vol', "-i %s --pdb %s --modes %s --sampling_rate %s -o %s --fixed_Gaussian %s"%\
                       (fnOutMeta, fnPseudo, fnModes, Ts, fnDeform, sigma))
            
        #score and distance matrix calculation         
        score = [[0 for i in volList] for i in volList]
        for voli in volList:
            elastAlign = xmipp.MetaData(self._getExtraPath("compDeformVol_%d.xmd"%voli.getObjId()))
            mdIter = md.iterRows(elastAlign)
            for volj in volList:
                if volj.getObjId() is voli.getObjId():
                    score[(voli.getObjId()-1)][(volj.getObjId()-1)] = 0
                    
                else:
                    elasticRow = next(mdIter)
                    maxCc = elasticRow.getValue(md.MDL_MAXCC)
                    score[(voli.getObjId()-1)][(volj.getObjId()-1)] = (1 - maxCc)
                                            
        print "score matrix is: "
        print score
        
        
        fnRoot = self._getExtraPath ("DistanceMatrixNormal.txt")   
        distance = [[0 for i in volList] for i in volList]
        for i in volList:
            for j in volList:
                distance[(i.getObjId()-1)][(j.getObjId()-1)] = (score[(i.getObjId()-1)][(j.getObjId()-1)] + score[(j.getObjId()-1)][(i.getObjId()-1)])/2
                fh = open(self._defineResultsName(),"a")
                fh.write("%f\n"%distance[(i.getObjId()-1)][(j.getObjId()-1)])
                fh.close()
                fh = open(fnRoot,"a")
                fh.write("%f\t"%distance[(i.getObjId()-1)][(j.getObjId()-1)])
                fh.close()  
            fh = open(fnRoot,"a")
            fh.write("\n")
            fh.close()                     
        
        print "distance matrix is: "
        print distance
                       
        #coordinate matrix calculation        
        mds = manifold.MDS(n_components=self.numberOfDimensions.get(), metric=True, max_iter=3000, eps=1e-9, dissimilarity="precomputed", n_jobs=1)
        embed3d = mds.fit(distance).embedding_    
                
                  
        print "embed3d = "
        print embed3d 
        
        
        for x in volList:
            for y in range(self.numberOfDimensions.get()):
                fh = open(self._getExtraPath ("CoordinateMatrix.txt"),"a")
                fh.write("%f\t"%embed3d[(x.getObjId()-1)][(y)])
                fh.close()  
            fh = open(self._getExtraPath ("CoordinateMatrix.txt"),"a")
            fh.write("\n")
            fh.close() 
        
                
        cleanPattern(self._getExtraPath('pseudoatoms*'))
        cleanPattern(self._getExtraPath('vec_ani.pkl'))                        
        
    #--------------------------- INFO functions --------------------------------------------
    
    def _validate(self):
        errors = []
        for pointer in self.inputVolumes:
            if pointer.pointsNone():
                errors.append('Invalid input, pointer: %s' % pointer.getObjValue())
                errors.append('              extended: %s' % pointer.getExtended())
        numberOfDimensions=self.numberOfDimensions.get()
        if numberOfDimensions > 3 or numberOfDimensions < 1:
            errors.append("The number of dimensions should be 1, 2 or, at most, 3.")
        
        return errors 
       
    #def _summary(self):
    #    summary = []
    #            
    #    return summary
    
    def _methods(self):
        messages = []
        messages.append('C.O.S. Sorzano et. al. "StructMap: Multivariate distance analysis of elastically aligned electron microscopy density maps\n'
                        '                         for exploring pathways of conformational changes"')
        return messages
        
    def _citations(self):
        return ['C.O.S.Sorzano2015']
    
    #--------------------------- UTILS functions --------------------------------------------
    def _iterInputVolumes(self):
        """ Iterate over all the input volumes. """
        for pointer in self.inputVolumes:
            item = pointer.get()
            if item is None:
                break
            itemId = item.getObjId()
            if isinstance(item, em.Volume):
                item.outputName = self._getExtraPath('output_vol%06d.vol' % itemId)
                yield item
            elif isinstance(item, em.SetOfVolumes):
                for vol in item:
                    vol.outputName = self._getExtraPath('output_vol%06d_%03d.vol' % (itemId, vol.getObjId()))
                    yield vol
                    
    def _getAlignArgs(self):
        alignArgs = ''
        alignArgs += " --local --rot %f %f 1 --tilt %f %f 1 --psi %f %f 1 -x %f %f 1 -y %f %f 1 -z %f %f 1 --scale %f %f 0.005" %\
                (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1)
                       
        return alignArgs
    
    def _defineResultsName(self):
        return self._getExtraPath('DistanceMatrixColumn.txt')
     
    